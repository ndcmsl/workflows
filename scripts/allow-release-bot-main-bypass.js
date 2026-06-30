#!/usr/bin/env node

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const API_VERSION = '2022-11-28';
const DEFAULT_ORG = 'ndcmsl';
const DEFAULT_BRANCH = 'main';
const RELEASE_BOT_APP_ID = 4176453;
const RELEASE_BOT_APP_SLUG = 'ndcmsl-release-bot';

const args = parseArgs(process.argv.slice(2));

if (args.help) {
  printHelp();
  process.exit(0);
}

const org = args.org || DEFAULT_ORG;
const branch = args.branch || DEFAULT_BRANCH;
const apply = Boolean(args.apply);
const includeArchived = Boolean(args.includeArchived);
const repoFilter = args.repo ? new Set(asArray(args.repo)) : null;
const token = readToken();

main().catch((error) => {
  log('fatal', error.stack || error.message);
  process.exit(1);
});

async function main() {
  log('info', `org=${org}`);
  log('info', `branch=${branch}`);
  log('info', `bot=${RELEASE_BOT_APP_SLUG} integration_id=${RELEASE_BOT_APP_ID}`);
  log('info', `mode=${apply ? 'apply' : 'dry-run'}`);

  const user = await gh('GET', '/user');
  log('info', `authenticated_as=${user.login}`);

  const repos = await listRepos();
  const selectedRepos = repos
    .filter((repo) => includeArchived || !repo.archived)
    .filter((repo) => !repoFilter || repoFilter.has(repo.name) || repoFilter.has(repo.full_name));

  let changed = 0;
  let unchanged = 0;
  let skipped = 0;
  let failed = 0;

  for (let index = 0; index < selectedRepos.length; index += 1) {
    const repo = selectedRepos[index];
    const prefix = `[${index + 1}/${selectedRepos.length}] ${repo.full_name}`;

    try {
      const result = await processRepo(repo, prefix);
      changed += result.changed;
      unchanged += result.unchanged;
      skipped += result.skipped;
    } catch (error) {
      failed += 1;
      log('error', `${prefix} ${error.message}`);
    }
  }

  log('summary', `changed=${changed} unchanged=${unchanged} skipped=${skipped} failed=${failed} mode=${apply ? 'apply' : 'dry-run'}`);
}

async function processRepo(repo, prefix) {
  log('repo', `${prefix} start`);

  const result = { changed: 0, unchanged: 0, skipped: 0 };
  const rulesetResult = await ensureRulesetBypass(repo, prefix);
  result[rulesetResult] += 1;

  const protectionResult = await ensureClassicProtectionBypass(repo, prefix);
  result[protectionResult] += 1;

  const pushRestrictionResult = await ensureClassicPushRestrictionApp(repo, prefix);
  result[pushRestrictionResult] += 1;

  return result;
}

async function ensureRulesetBypass(repo, prefix) {
  const summaries = await gh('GET', `/repos/${repo.full_name}/rulesets?per_page=100`);
  const branchRulesets = summaries.filter((ruleset) => ruleset.target === 'branch');

  if (branchRulesets.length === 0) {
    log('skip', `${prefix} rulesets=none`);
    return 'skipped';
  }

  let matched = false;
  let changed = false;

  for (const summary of branchRulesets) {
    const ruleset = await gh('GET', `/repos/${repo.full_name}/rulesets/${summary.id}`);
    const applies = rulesetAppliesToBranch(ruleset, branch, repo.default_branch);
    log('ruleset', `${prefix} id=${ruleset.id} name="${ruleset.name}" enforcement=${ruleset.enforcement} applies=${applies}`);

    if (!applies) continue;
    matched = true;

    const bypassActors = Array.isArray(ruleset.bypass_actors) ? ruleset.bypass_actors : [];
    const alreadyPresent = bypassActors.some((actor) => (
      actor.actor_type === 'Integration' &&
      Number(actor.actor_id) === RELEASE_BOT_APP_ID &&
      actor.bypass_mode === 'always'
    ));

    if (alreadyPresent) {
      log('ok', `${prefix} ruleset=${ruleset.id} bot bypass already present`);
      continue;
    }

    if (!apply) {
      log('dry-run', `${prefix} would add ruleset bypass to ruleset=${ruleset.id}`);
      changed = true;
      continue;
    }

    const body = sanitizeRulesetForUpdate(ruleset);
    body.bypass_actors = [
      ...bypassActors.map((actor) => ({
        actor_id: normalizeActorId(actor.actor_id),
        actor_type: actor.actor_type,
        bypass_mode: actor.bypass_mode,
      })),
      {
        actor_id: RELEASE_BOT_APP_ID,
        actor_type: 'Integration',
        bypass_mode: 'always',
      },
    ];

    await gh('PUT', `/repos/${repo.full_name}/rulesets/${ruleset.id}`, body);
    log('changed', `${prefix} added ruleset bypass to ruleset=${ruleset.id}`);
    changed = true;
  }

  if (!matched) {
    log('skip', `${prefix} no ruleset applies to ${branch}`);
    return 'skipped';
  }

  return changed ? 'changed' : 'unchanged';
}

async function ensureClassicProtectionBypass(repo, prefix) {
  let protection;
  try {
    protection = await gh('GET', `/repos/${repo.full_name}/branches/${branch}/protection`);
  } catch (error) {
    if (error.status === 404) {
      log('skip', `${prefix} classic protection=none`);
      return 'skipped';
    }
    throw error;
  }

  if (!protection.required_pull_request_reviews) {
    log('skip', `${prefix} classic protection has no required PR reviews`);
    return 'skipped';
  }

  const apps = protection.required_pull_request_reviews.bypass_pull_request_allowances?.apps || [];
  const alreadyPresent = apps.some((app) => app.id === RELEASE_BOT_APP_ID || app.slug === RELEASE_BOT_APP_SLUG);

  if (alreadyPresent) {
    log('ok', `${prefix} classic protection bot bypass already present`);
    return 'unchanged';
  }

  if (!apply) {
    log('dry-run', `${prefix} would add classic protection bypass app=${RELEASE_BOT_APP_SLUG}`);
    return 'changed';
  }

  await gh(
    'POST',
    `/repos/${repo.full_name}/branches/${branch}/protection/required_pull_request_reviews/bypass_pull_request_allowances/apps`,
    { apps: [RELEASE_BOT_APP_SLUG] },
  );
  log('changed', `${prefix} added classic protection bypass app=${RELEASE_BOT_APP_SLUG}`);
  return 'changed';
}

async function ensureClassicPushRestrictionApp(repo, prefix) {
  let protection;
  try {
    protection = await gh('GET', `/repos/${repo.full_name}/branches/${branch}/protection`);
  } catch (error) {
    if (error.status === 404) return 'skipped';
    throw error;
  }

  if (!protection.restrictions) {
    log('skip', `${prefix} push restrictions=none`);
    return 'skipped';
  }

  const apps = protection.restrictions.apps || [];
  const alreadyPresent = apps.some((app) => app.id === RELEASE_BOT_APP_ID || app.slug === RELEASE_BOT_APP_SLUG);

  if (alreadyPresent) {
    log('ok', `${prefix} push restriction app already present`);
    return 'unchanged';
  }

  if (!apply) {
    log('dry-run', `${prefix} would add push restriction app=${RELEASE_BOT_APP_SLUG}`);
    return 'changed';
  }

  await gh(
    'POST',
    `/repos/${repo.full_name}/branches/${branch}/protection/restrictions/apps`,
    [RELEASE_BOT_APP_SLUG],
  );
  log('changed', `${prefix} added push restriction app=${RELEASE_BOT_APP_SLUG}`);
  return 'changed';
}

function rulesetAppliesToBranch(ruleset, branchName, defaultBranch) {
  const refName = ruleset.conditions && ruleset.conditions.ref_name;
  if (!refName) return true;

  const includes = refName.include || refName.includes || [];
  const excludes = refName.exclude || refName.excludes || [];
  const ref = `refs/heads/${branchName}`;

  const included = includes.length === 0 || includes.some((pattern) => refPatternMatches(pattern, ref, branchName, defaultBranch));
  const excluded = excludes.some((pattern) => refPatternMatches(pattern, ref, branchName, defaultBranch));
  return included && !excluded;
}

function refPatternMatches(pattern, ref, branchName, defaultBranch) {
  if (pattern === '~ALL') return true;
  if (pattern === '~DEFAULT_BRANCH') return branchName === defaultBranch;
  if (pattern === ref || pattern === branchName) return true;

  const normalized = pattern.startsWith('refs/heads/') ? pattern : `refs/heads/${pattern}`;
  const escaped = normalized.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
  return new RegExp(`^${escaped}$`).test(ref);
}

function sanitizeRulesetForUpdate(ruleset) {
  return {
    name: ruleset.name,
    target: ruleset.target,
    enforcement: ruleset.enforcement,
    bypass_actors: [],
    conditions: ruleset.conditions,
    rules: ruleset.rules || [],
  };
}

async function listRepos() {
  const repos = [];

  for (let page = 1; ; page += 1) {
    const batch = await gh('GET', `/orgs/${org}/repos?type=all&sort=full_name&per_page=100&page=${page}`);
    if (!Array.isArray(batch) || batch.length === 0) break;
    repos.push(...batch);
    log('page', `repos page=${page} count=${batch.length} total=${repos.length}`);
  }

  return repos;
}

async function gh(method, apiPath, body) {
  const response = await fetch(`https://api.github.com${apiPath}`, {
    method,
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      'User-Agent': 'ndcmsl-release-bot-bypass-script',
      'X-GitHub-Api-Version': API_VERSION,
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const message = data && data.message ? data.message : response.statusText;
    const error = new Error(`${method} ${apiPath} failed: ${response.status} ${message}`);
    error.status = response.status;
    throw error;
  }

  return data;
}

function readToken() {
  if (process.env.GH_ADMIN_TOKEN) return process.env.GH_ADMIN_TOKEN.trim();
  if (process.env.GH_TOKEN) return process.env.GH_TOKEN.trim();
  if (process.env.GITHUB_TOKEN) return process.env.GITHUB_TOKEN.trim();

  const npmrcPath = args.npmrc || path.join(os.homedir(), '.npmrc');
  if (fs.existsSync(npmrcPath)) {
    const npmrc = fs.readFileSync(npmrcPath, 'utf8');
    const match = npmrc.match(/_authToken=([^\r\n]+)/);
    if (match) return match[1].trim();
  }

  throw new Error('No token found. Set GH_ADMIN_TOKEN or keep a GitHub token in ~/.npmrc');
}

function parseArgs(argv) {
  const parsed = {};

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--apply') {
      parsed.apply = true;
    } else if (arg === '--include-archived') {
      parsed.includeArchived = true;
    } else if (arg === '--help' || arg === '-h') {
      parsed.help = true;
    } else if (arg.startsWith('--')) {
      const key = arg.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      const next = argv[index + 1];
      if (!next || next.startsWith('--')) throw new Error(`Missing value for ${arg}`);
      if (parsed[key] === undefined) parsed[key] = next;
      else parsed[key] = [...asArray(parsed[key]), next];
      index += 1;
    } else {
      throw new Error(`Unknown argument ${arg}`);
    }
  }

  return parsed;
}

function asArray(value) {
  return Array.isArray(value) ? value : [value];
}

function normalizeActorId(value) {
  if (value === null || value === undefined || value === 'null') return null;
  const number = Number(value);
  if (!Number.isInteger(number)) throw new Error(`Invalid actor id: ${value}`);
  return number;
}

function log(level, message) {
  console.log(`${new Date().toISOString()} [${level}] ${message}`);
}

function printHelp() {
  console.log(`
Usage:
  node scripts/allow-release-bot-main-bypass.js [options]

Adds ndcmsl-release-bot as bypass actor for main/default-branch protections.
Dry-run by default. Add --apply to update GitHub.

Options:
  --org <org>                 GitHub org. Default: ${DEFAULT_ORG}
  --branch <branch>           Branch to match. Default: ${DEFAULT_BRANCH}
  --repo <owner/name|name>    Limit to one repo. Can be repeated.
  --include-archived          Include archived repositories.
  --npmrc <path>              Read token from a specific .npmrc file.
  --apply                     Apply changes.

Token lookup:
  1. GH_ADMIN_TOKEN
  2. GH_TOKEN
  3. GITHUB_TOKEN
  4. ~/.npmrc _authToken

Examples:
  node scripts/allow-release-bot-main-bypass.js
  node scripts/allow-release-bot-main-bypass.js --repo ndcmsl/ecom.notification
  node scripts/allow-release-bot-main-bypass.js --apply
`);
}
