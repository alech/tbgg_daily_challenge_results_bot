import { execFileSync } from 'node:child_process';
import { cpSync } from 'node:fs';
import * as path from 'node:path';
import * as lambda from 'aws-cdk-lib/aws-lambda';

/**
 * Lambda runs Python 3.13 on Graviton. pycord pulls in aiohttp, multidict, frozenlist and
 * propcache, all of which ship compiled extensions, so wheels built on a developer's macOS
 * machine will not load. uv resolves Linux wheels directly via --python-platform, which keeps
 * the common path container-free; the container fallback exists for machines without uv.
 */
const PYTHON_PLATFORM = 'aarch64-manylinux2014';
const PYTHON_VERSION = '3.13';
const REQUIREMENTS = 'requirements-lambda.txt';
const PACKAGE_DIR = path.join('src', 'tbgg_bot');

function hasUv(cwd: string): boolean {
  try {
    execFileSync('uv', ['--version'], { cwd, stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

/**
 * Install pinned Linux/arm64 wheels and the package itself into the asset output directory.
 */
function bundleWithUv(projectRoot: string, outputDir: string): void {
  execFileSync(
    'uv',
    [
      'pip',
      'install',
      '--quiet',
      '--target',
      outputDir,
      '--python-platform',
      PYTHON_PLATFORM,
      '--python-version',
      PYTHON_VERSION,
      '--only-binary=:all:',
      '-r',
      path.join(projectRoot, REQUIREMENTS),
    ],
    { cwd: projectRoot, stdio: 'inherit' },
  );
  // Skip __pycache__: those .pyc files were compiled on the developer's machine and only
  // bloat the asset, which changes the asset hash on every local test run.
  cpSync(path.join(projectRoot, PACKAGE_DIR), path.join(outputDir, 'tbgg_bot'), {
    recursive: true,
    filter: (source) => !source.includes('__pycache__'),
  });
}

/**
 * Asset bundling for the handler: uv locally, otherwise the Lambda build image.
 *
 * Set CDK_DOCKER=finch (or podman) to use a non-Docker container runtime for the fallback.
 */
export function handlerCode(projectRoot: string): lambda.Code {
  return lambda.Code.fromAsset(projectRoot, {
    exclude: [
      'infra',
      'tests',
      '.venv',
      '.git',
      '.ruff_cache',
      '.mypy_cache',
      '.pytest_cache',
      '**/__pycache__',
      '*.md',
    ],
    bundling: {
      image: lambda.Runtime.PYTHON_3_13.bundlingImage,
      platform: 'linux/arm64',
      command: [
        'bash',
        '-c',
        [
          `pip install --no-cache-dir -r /asset-input/${REQUIREMENTS} -t /asset-output`,
          `cp -r /asset-input/${PACKAGE_DIR} /asset-output/tbgg_bot`,
        ].join(' && '),
      ],
      local: {
        tryBundle(outputDir: string): boolean {
          if (!hasUv(projectRoot)) {
            return false;
          }
          bundleWithUv(projectRoot, outputDir);
          return true;
        },
      },
    },
  });
}
