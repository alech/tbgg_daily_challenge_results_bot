import * as cdk from 'aws-cdk-lib';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import { Construct } from 'constructs';
import { handlerCode } from './bundling';

export interface TbggBotStackProps extends cdk.StackProps {
  /** Discord channel the daily result is posted to. */
  readonly discordChannelId: string;
  /** Discord user who receives failure and cookie-expiry DMs. */
  readonly discordAlertUserId: string;
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

const GEOGUESSR_PREFIX = '/tbgg-bot/geoguessr';
const DISCORD_TOKEN_PARAM = '/tbgg-bot/discord/token';
const COOKIE_PARAM = `${GEOGUESSR_PREFIX}/ncfa`;

export class TbggBotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: TbggBotStackProps) {
    super(scope, id, props);

    // Credentials live in SSM SecureString parameters, which CloudFormation cannot create.
    // They are written once with the AWS CLI (see the README) and only referenced here, so no
    // secret material ever appears in the template and a deploy can never overwrite them.
    const parameterArn = (name: string): string =>
      this.formatArn({
        service: 'ssm',
        resource: 'parameter',
        resourceName: name.replace(/^\//, ''),
      });

    const logGroup = new logs.LogGroup(this, 'DailyResultLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const handler = new lambda.Function(this, 'DailyResult', {
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'tbgg_bot.handler.lambda_handler',
      code: handlerCode(props.projectRoot),
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      logGroup,
      environment: {
        GEOGUESSR_PARAM_PREFIX: GEOGUESSR_PREFIX,
        DISCORD_TOKEN_PARAM: DISCORD_TOKEN_PARAM,
        DISCORD_CHANNEL_ID: props.discordChannelId,
        DISCORD_ALERT_USER_ID: props.discordAlertUserId,
      },
    });

    // Read-only, and only these two parameters. Nothing in this project writes the cookie:
    // GeoGuessr signs in with emailed one-time codes, so refreshing it is a human pasting a
    // new value in.
    handler.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [parameterArn(COOKIE_PARAM), parameterArn(DISCORD_TOKEN_PARAM)],
      }),
    );

    // Decryption needs no explicit kms:Decrypt grant: the AWS-managed aws/ssm key's own policy
    // allows principals in this account to use it when the call goes through SSM.

    new events.Rule(this, 'DailySchedule', {
      description: 'Post the TBGG club daily-challenge result just after the challenge closes',
      schedule: SCHEDULE,
      targets: [new targets.LambdaFunction(handler, { retryAttempts: 2 })],
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

    new cdk.CfnOutput(this, 'FunctionName', { value: handler.functionName });
  }
}
