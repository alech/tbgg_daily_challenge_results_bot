import * as cdk from 'aws-cdk-lib';
import * as route53 from 'aws-cdk-lib/aws-route53';
import { Construct } from 'constructs';

export interface TbggDnsStackProps extends cdk.StackProps {
  /** The name delegated to Route 53 from its parent zone, e.g. site.example.com. */
  readonly domain: string;
}

/**
 * The public hosted zone the site's domain is delegated to.
 *
 * A stack of its own because it must exist before the main stack can deploy: the site's
 * certificate is validated through this zone, which only works once the parent zone's NS
 * record points here. Deploy this, delegate to the NameServers output, then deploy the rest.
 */
export class TbggDnsStack extends cdk.Stack {
  readonly zone: route53.IHostedZone;

  constructor(scope: Construct, id: string, props: TbggDnsStackProps) {
    super(scope, id, props);

    const zone = new route53.PublicHostedZone(this, 'Zone', {
      zoneName: props.domain,
      comment: 'TBGG bot site, delegated from the parent domain',
    });
    // Recreating the zone would hand out new nameservers and silently break the delegation
    zone.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
    this.zone = zone;

    new cdk.CfnOutput(this, 'NameServers', {
      value: cdk.Fn.join(' ', zone.hostedZoneNameServers ?? []),
      description: `Delegate ${props.domain} to these with an NS record in the parent zone`,
    });
  }
}
