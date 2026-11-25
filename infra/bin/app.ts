#!/usr/bin/env node
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { TbggBotStack } from '../lib/tbgg-bot-stack';
import { TbggDnsStack } from '../lib/tbgg-dns-stack';

const app = new cdk.App();

function requiredContext(key: string): string {
  const value = String(app.node.tryGetContext(key) ?? '').trim();
  if (!value) {
    throw new Error(
      `${key} is not set. Pass it with:\n` +
        `  cdk deploy -c ${key}=123456789012345678\n` +
        'or set it in the "context" block of infra/cdk.json, or of infra/cdk.context.json\n' +
        'for values that must stay out of git.',
    );
  }
  return value;
}

const discordChannelIds = requiredContext('discordChannelIds');
const discordAlertUserId = requiredContext('discordAlertUserId');
const weeklyChannelIds = requiredContext('weeklyChannelIds');
// The repo is public, so these live in infra/cdk.context.json, which git ignores
const budgetEmail = requiredContext('budgetEmail');
const budgetPagerEmail = requiredContext('budgetPagerEmail');
const siteDomain = requiredContext('siteDomain');
// The page's unguessable path on the otherwise public site, e.g. ladder_<22 random chars>
const ladderPath = requiredContext('ladderPath');
if (!/^[A-Za-z0-9_-]{16,}$/.test(ladderPath)) {
  throw new Error('ladderPath must be at least 16 URL-safe characters: letters, digits, _ or -');
}

// Deploy into whichever account and region the current CLI credentials point at.
const env: cdk.Environment = {
  ...(process.env.CDK_DEFAULT_ACCOUNT ? { account: process.env.CDK_DEFAULT_ACCOUNT } : {}),
  ...(process.env.CDK_DEFAULT_REGION ? { region: process.env.CDK_DEFAULT_REGION } : {}),
};

const dns = new TbggDnsStack(app, 'TbggDnsStack', {
  domain: siteDomain,
  env,
  description: `Route 53 zone for ${siteDomain}, delegated from its parent domain`,
});

new TbggBotStack(app, 'TbggBotStack', {
  discordChannelIds,
  discordAlertUserId,
  weeklyChannelIds,
  budgetEmail,
  budgetPagerEmail,
  siteDomain,
  ladderPath,
  hostedZone: dns.zone,
  projectRoot: path.resolve(__dirname, '..', '..'),
  env,
  description: 'Posts the TBGG GeoGuessr club daily-challenge team result to Discord',
});
