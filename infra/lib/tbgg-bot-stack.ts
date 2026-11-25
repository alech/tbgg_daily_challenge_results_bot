import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as pricingplanmanager from 'aws-cdk-lib/aws-pricingplanmanager';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as route53targets from 'aws-cdk-lib/aws-route53-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';
import { handlerCode } from './bundling';

export interface TbggBotStackProps extends cdk.StackProps {
  /** Comma-separated Discord channels the daily result is posted to. */
  readonly discordChannelIds: string;
  /** Discord user who receives failure and cookie-expiry DMs. */
  readonly discordAlertUserId: string;
  /** Comma-separated Discord channels the weekly summary is posted to. */
  readonly weeklyChannelIds: string;
  /** Emailed once the account's spend this month passes BUDGET_ALERT_USD. */
  readonly budgetEmail: string;
  /** Emailed once the account's spend this month passes BUDGET_PAGE_USD. */
  readonly budgetPagerEmail: string;
  /** The site's domain; its root is a placeholder page. */
  readonly siteDomain: string;
  /** The Ladder Watch page's path on siteDomain; unguessable, and kept out of git. */
  readonly ladderPath: string;
  /** The Route 53 zone siteDomain is delegated to (see TbggDnsStack). */
  readonly hostedZone: route53.IHostedZone;
  /** Absolute path to the Python project root. */
  readonly projectRoot: string;
}

/**
 * The GeoGuessr daily challenge closes at 23:59:59 UTC, so the run fires just after
 * midnight and reports on the day that has just ended. Nobody's late round is missed.
 */
const SCHEDULE = events.Schedule.cron({ minute: '5', hour: '0', day: '*', month: '*', year: '*' });

/**
 * The cookie check runs at 20:00 local, leaving the evening to paste a fresh cookie in before
 * the nightly post. It must follow German DST, and EventBridge *Rules* are UTC-only — hence
 * EventBridge Scheduler here, which supports a timezone, while the UTC-anchored daily post
 * stays on a Rule.
 */
const CHECK_CRON = 'cron(0 20 * * ? *)';
const CHECK_TIMEZONE = 'Europe/Berlin';

/** Club XP is sampled on the hour to chart the global club leaderboard over time. */
const CLUB_XP_SCHEDULE = events.Schedule.cron({ minute: '0' });
/** The page is rebuilt five minutes after each snapshot, so it always includes it. */
const LADDER_PAGE_SCHEDULE = events.Schedule.cron({ minute: '5' });
/**
 * Weekly club challenges close on Wednesday at 11:00 UTC; the snapshot that closes the week
 * lands 30 seconds later, so the summary goes out at 11:05.
 */
const WEEKLY_SCHEDULE = events.Schedule.cron({ minute: '5', hour: '11', weekDay: 'WED' });

/** Requests one address may make in five minutes before WAF blocks it for a while. */
const RATE_LIMIT_PER_5_MIN = 300;
const BUDGET_ALERT_USD = 10;
const BUDGET_PAGE_USD = 500;

const GEOGUESSR_PREFIX = '/tbgg-bot/geoguessr';
const DISCORD_TOKEN_PARAM = '/tbgg-bot/discord/token';
const COOKIE_PARAM = `${GEOGUESSR_PREFIX}/ncfa`;

export class TbggBotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: TbggBotStackProps) {
    super(scope, id, props);
    // CloudFront only accepts certificates from us-east-1, and this stack issues its own
    if (!cdk.Token.isUnresolved(this.region) && this.region !== 'us-east-1') {
      throw new Error(`TbggBotStack must deploy to us-east-1, not ${this.region}`);
    }

    // The XP history cannot be re-fetched after the fact, so the bucket outlives the stack.
    const clubXpBucket = new s3.Bucket(this, 'ClubXpHistory', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.ladderSite(props, clubXpBucket);
    const ladderUrl = `https://${props.siteDomain}/${props.ladderPath}`;
    const daily = this.dailyFunction(props, clubXpBucket, ladderUrl);
    const clubXp = this.clubXpFunction(props.projectRoot, clubXpBucket);
    this.costAlerts(props);

    new cdk.CfnOutput(this, 'FunctionName', { value: daily.functionName });
    new cdk.CfnOutput(this, 'ClubXpFunctionName', { value: clubXp.functionName });
    new cdk.CfnOutput(this, 'ClubXpBucketName', { value: clubXpBucket.bucketName });
    new cdk.CfnOutput(this, 'LadderUrl', { value: ladderUrl });
  }

  // Credentials live in SSM SecureString parameters, which CloudFormation cannot create.
  // They are written once with the AWS CLI (see the README) and only referenced here, so no
  // secret material ever appears in the template and a deploy can never overwrite them.
  private parameterArn(name: string): string {
    return this.formatArn({
      service: 'ssm',
      resource: 'parameter',
      resourceName: name.replace(/^\//, ''),
    });
  }

  private pythonFunction(
    id: string,
    projectRoot: string,
    handler: string,
    options: {
      timeout: number;
      memorySize: number;
      environment: Record<string, string>;
      /** Keeps an existing log group's logical ID, so a refactor does not replace it. */
      logGroupId?: string;
    },
  ): lambda.Function {
    const logGroup = new logs.LogGroup(this, options.logGroupId ?? `${id}Logs`, {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    return new lambda.Function(this, id, {
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler,
      code: handlerCode(projectRoot),
      timeout: cdk.Duration.seconds(options.timeout),
      memorySize: options.memorySize,
      logGroup,
      environment: options.environment,
    });
  }

  /** The nightly post, the evening cookie check and the weekly summary. */
  private dailyFunction(
    props: TbggBotStackProps,
    clubXpBucket: s3.Bucket,
    ladderUrl: string,
  ): lambda.Function {
    const entry = 'tbgg_bot.handler.lambda_handler';
    const handler = this.pythonFunction('DailyResult', props.projectRoot, entry, {
      timeout: 60,
      memorySize: 512,
      environment: {
        GEOGUESSR_PARAM_PREFIX: GEOGUESSR_PREFIX,
        DISCORD_TOKEN_PARAM: DISCORD_TOKEN_PARAM,
        DISCORD_CHANNEL_IDS: props.discordChannelIds,
        DISCORD_ALERT_USER_ID: props.discordAlertUserId,
        WEEKLY_CHANNEL_IDS: props.weeklyChannelIds,
        CLUB_XP_BUCKET: clubXpBucket.bucketName,
        LADDER_URL: ladderUrl,
      },
    });

    handler.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [this.parameterArn(COOKIE_PARAM), this.parameterArn(DISCORD_TOKEN_PARAM)],
      }),
    );
    // The nightly post stores the cookie GeoGuessr reissues, because sessions lapse well
    // before the cookie's claimed one-year expiry. Only the cookie: the token stays read-only.
    handler.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:PutParameter'],
        resources: [this.parameterArn(COOKIE_PARAM)],
      }),
    );
    // Neither decryption nor encryption needs a KMS grant: the AWS-managed aws/ssm key's own
    // policy allows principals in this account to use it when the call goes through SSM.

    // The weekly summary reads the two snapshots that bound the week
    clubXpBucket.grantRead(handler);

    new events.Rule(this, 'DailySchedule', {
      description: 'Post the TBGG club daily-challenge result just after the challenge closes',
      schedule: SCHEDULE,
      targets: [new targets.LambdaFunction(handler, { retryAttempts: 2 })],
    });
    new events.Rule(this, 'WeeklySchedule', {
      description: "Post TBGG's XP gain against its neighbours when the challenge week closes",
      schedule: WEEKLY_SCHEDULE,
      targets: [
        new targets.LambdaFunction(handler, {
          retryAttempts: 2,
          event: events.RuleTargetInput.fromObject({ action: 'weekly' }),
        }),
      ],
    });

    const schedulerRole = new iam.Role(this, 'CookieCheckRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'Lets EventBridge Scheduler invoke the cookie health check',
    });
    handler.grantInvoke(schedulerRole);

    new scheduler.CfnSchedule(this, 'CookieHealthCheck', {
      description: 'Warn by DM if the GeoGuessr cookie has expired, while there is time to fix it',
      flexibleTimeWindow: { mode: 'OFF' },
      scheduleExpression: CHECK_CRON,
      scheduleExpressionTimezone: CHECK_TIMEZONE,
      target: {
        arn: handler.functionArn,
        roleArn: schedulerRole.roleArn,
        input: JSON.stringify({ action: 'check' }),
        retryPolicy: { maximumEventAgeInSeconds: 3600, maximumRetryAttempts: 2 },
      },
    });
    return handler;
  }

  /**
   * A function of its own rather than another action on the daily one: it needs S3 but not
   * the Discord token, and its hourly failures should not count against the nightly post.
   */
  private clubXpFunction(projectRoot: string, clubXpBucket: s3.Bucket): lambda.Function {
    const entry = 'tbgg_bot.club_xp.lambda_handler';
    const clubXp = this.pythonFunction('ClubXpSnapshot', projectRoot, entry, {
      timeout: 60,
      memorySize: 256,
      logGroupId: 'ClubXpLogs',
      environment: {
        GEOGUESSR_PARAM_PREFIX: GEOGUESSR_PREFIX,
        CLUB_XP_BUCKET: clubXpBucket.bucketName,
      },
    });
    clubXp.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [this.parameterArn(COOKIE_PARAM)],
      }),
    );
    // Write-only: it adds snapshots and never needs to read or delete one.
    clubXpBucket.grantPut(clubXp);

    new events.Rule(this, 'ClubXpSchedule', {
      description: 'Snapshot the global GeoGuessr club XP leaderboard',
      schedule: CLUB_XP_SCHEDULE,
      targets: [new targets.LambdaFunction(clubXp, { retryAttempts: 2 })],
    });
    return clubXp;
  }

  /**
   * The Ladder Watch page: rebuilt hourly into a private bucket that only CloudFront can
   * read, and served on CloudFront's flat-rate Free plan. That plan has no overage charges,
   * so a flood of requests cannot run up a bill; WAF, which the plan requires and includes,
   * blocks any single address that floods it.
   */
  private ladderSite(props: TbggBotStackProps, clubXpBucket: s3.Bucket): cloudfront.Distribution {
    const { projectRoot, siteDomain, hostedZone } = props;
    // Separate from the history, so a mistake in this bucket's policy cannot expose snapshots.
    // Its only content is rebuilt every hour, so nothing is lost when it goes.
    const siteBucket = new s3.Bucket(this, 'LadderSiteContent', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Flat-rate plans require a web ACL in the CloudFront scope, which lives in us-east-1
    const webAcl = new wafv2.CfnWebACL(this, 'LadderSiteFirewall', {
      scope: 'CLOUDFRONT',
      defaultAction: { allow: {} },
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: 'LadderSiteFirewall',
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          name: 'RateLimitPerAddress',
          priority: 0,
          action: { block: {} },
          statement: {
            rateBasedStatement: { limit: RATE_LIMIT_PER_5_MIN, aggregateKeyType: 'IP' },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'RateLimitPerAddress',
            sampledRequestsEnabled: true,
          },
        },
      ],
    });

    // Validated through the delegated zone, so the delegation must be live before this deploys
    const certificate = new acm.Certificate(this, 'SiteCertificate', {
      domainName: siteDomain,
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    const distribution = new cloudfront.Distribution(this, 'LadderSite', {
      comment: 'TBGG Ladder Watch',
      domainNames: [siteDomain],
      certificate,
      defaultRootObject: 'index.html',
      defaultBehavior: {
        // Bucket declares isWebsite optional while IBucket requires it, which only
        // exactOptionalPropertyTypes notices; the object is a real IBucket at runtime.
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket as unknown as s3.IBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        // Honours the page's own five-minute Cache-Control
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
        compress: true,
      },
      // No priceClass: the flat-rate plan's price does not depend on which edges serve, and
      // the Free tier rejected a distribution restricted to PriceClass_100
      webAclId: webAcl.attrArn,
    });

    const alias = route53.RecordTarget.fromAlias(new route53targets.CloudFrontTarget(distribution));
    new route53.ARecord(this, 'SiteAlias', { zone: hostedZone, target: alias });
    new route53.AaaaRecord(this, 'SiteAliasV6', { zone: hostedZone, target: alias });

    // The plan covers the hosted zone too, so its monthly charge and queries are included
    new pricingplanmanager.CfnSubscription(this, 'LadderSitePlan', {
      planFamily: 'CloudFront',
      planTier: 'FREE',
      usageLevel: 'DEFAULT',
      resourceArns: [distribution.distributionArn, webAcl.attrArn, hostedZone.hostedZoneArn],
    });

    const entry = 'tbgg_bot.ladder_page.lambda_handler';
    const builder = this.pythonFunction('LadderPage', projectRoot, entry, {
      // Up to eight weeks of hourly snapshots, fetched in parallel
      timeout: 120,
      memorySize: 512,
      environment: {
        CLUB_XP_BUCKET: clubXpBucket.bucketName,
        SITE_BUCKET: siteBucket.bucketName,
        LADDER_PATH: props.ladderPath,
      },
    });
    clubXpBucket.grantRead(builder);
    siteBucket.grantPut(builder);

    new events.Rule(this, 'LadderPageSchedule', {
      description: 'Rebuild the Ladder Watch page after each club XP snapshot',
      schedule: LADDER_PAGE_SCHEDULE,
      targets: [new targets.LambdaFunction(builder, { retryAttempts: 2 })],
    });
    return distribution;
  }

  /** Two email alarms on the whole account's monthly spend, actual not forecast. */
  private costAlerts(props: TbggBotStackProps): void {
    type Alert = budgets.CfnBudget.NotificationWithSubscribersProperty;
    const alert = (threshold: number, address: string): Alert => ({
      notification: {
        notificationType: 'ACTUAL',
        comparisonOperator: 'GREATER_THAN',
        threshold,
        thresholdType: 'ABSOLUTE_VALUE',
      },
      subscribers: [{ subscriptionType: 'EMAIL', address }],
    });
    new budgets.CfnBudget(this, 'MonthlySpend', {
      budget: {
        budgetName: 'tbgg-bot-monthly-spend',
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: BUDGET_ALERT_USD, unit: 'USD' },
      },
      notificationsWithSubscribers: [
        alert(BUDGET_ALERT_USD, props.budgetEmail),
        alert(BUDGET_PAGE_USD, props.budgetPagerEmail),
      ],
    });
  }
}
