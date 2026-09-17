#!/usr/bin/env node
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { TbggBotStack } from '../lib/tbgg-bot-stack';

const app = new cdk.App();

function requiredContext(key: string): string {
  const value = String(app.node.tryGetContext(key) ?? '').trim();
  if (!value) {
    throw new Error(
      `${key} is not set. Pass it with:\n` +
        `  cdk deploy -c ${key}=123456789012345678\n` +
        'or set it in the "context" block of infra/cdk.json.',
    );
  }
  return value;
}

const discordChannelIds = requiredContext('discordChannelIds');
const discordAlertUserId = requiredContext('discordAlertUserId');

// Deploy into whichever account and region the current CLI credentials point at.
const env: cdk.Environment = {
  ...(process.env.CDK_DEFAULT_ACCOUNT ? { account: process.env.CDK_DEFAULT_ACCOUNT } : {}),
  ...(process.env.CDK_DEFAULT_REGION ? { region: process.env.CDK_DEFAULT_REGION } : {}),
};

new TbggBotStack(app, 'TbggBotStack', {
  discordChannelIds,
  discordAlertUserId,
  projectRoot: path.resolve(__dirname, '..', '..'),
  env,
  description: 'Posts the TBGG GeoGuessr club daily-challenge team result to Discord',
});
