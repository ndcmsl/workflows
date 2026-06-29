#!/usr/bin/env node

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const API_VERSION = '2022-11-28';
const DEFAULT_ORG = 'ndcmsl';
const DEFAULT_BRANCH = 'main';
const DEFAULT_ACTOR_TYPE = 'Integration';
const DEFAULT_BYPASS_MODE = 'always';

const args = parseArgs(process.argv.slice(2));

if (args.help) {
  printHelp();
  process.exit(0);
}

const org = args.org || DEFAULT_ORG;
const branch = args.branch || DEFAULT_BRANCH;
const actorType = args.actorType || DEFAULT_ACTOR_TYPE;
const actorId = normalizeActorId(args.actorId);
const bypassMode = args.bypassMode || DEFAULT_BYPASS_MODE;
const apply = Boolean(args.apply);
const repoFilter = args.repo ? new Set(asArray(args.repo)) : null;
const includeArchived = Boolean(args.includeArchived);
const token = readToken();

main().catch((error) => {
  log('fatal', error.stack || error.message);
  process.exit(1);
});

async function main() {
  log('info', `org=${org}`);
  log('info', `branch=${branch}`);
  log('info', `actor=${actorType}:${actorId ?? 'null'} bypass_mode=${bypassMode}`);
  log('info', `mode=${apply ? 'apply' : 'dry-run'}`);

  if (apply && args.actorId === undefined) {
    throw new Error('Refusing to apply without --actor-id. Pass the bot user/team/app actor id explicitly.');
  }

  const user = await gh('GET', '/user');
  log('info', `authenticated_as=${user.login}`);

  const repos = await listRepos();
  const selectedRepos = repos
    .filter((repo) => includeArchived || !repo.archived)
    .filter((repo) => !repoFilter || repoFilter.has(repo.name) || repoFilter.has(repo.full_name));

  log('info', `repos_found=${repos.length} repos_selected=${selectedRepos.length}`);

  let changed = 0;
  let unchanged = 0;
  let skipped = 0;
  let failed = 0;

  for (let index = 0; index < selectedRepos.length; index += 1) {
    const repo = selectedRepos[index];
    const prefix = `[${index + 1}/${selectedRepos.length}] ${repo.full_name}`;
    log('repo', `${prefix} start`);

    try {
      const result = await processRepo(repo, prefix);
      if (result === 'changed') changed += 1;
      else if (result === 'unchanged') unchanged += 1;
      else skipped += 1;
    } catch (error) {
      failed += 1;
      log('error', `${prefix} ${error.message}`);
    }
  }

  log(
    'summary',
    `changed=${changed} unchanged=${unchanged} skipped=${skipped} failed=${failed} mode=${apply ? 'apply' : 'dry-run'}`,
  );
}

async function processRepo(repo, prefix) {
  const rulesets = await gh('GET', `/repos/${repo.full_name}/rulesets?per_page=100`);
  const branchRulesets = rulesets.filter((ruleset) => ruleset.target === 'branch');

  if (branchRulesets.length === 0) {
    log('skip', `${prefix} no repository branch rulesets`);
    return 'skipped';
  }

  let repoChanged = false;
  let repoMatched = false;

  for (const summary of branchRulesets) {
    const ruleset = await gh('GET', `/repos/${repo.full_name}/rulesets/${summary.id}`);
    const applies = rulesetAppliesToBranch(ruleset, branch);

    log(
      'ruleset',
      `${prefix} id=${ruleset.id} name="${ruleset.name}" enforcement=${ruleset.enforcement} applies_to_${branch}=${applies}`,
    );

    if (!applies) continue;
    repoMatched = true;

    const bypassActors = Array.isArray(ruleset.bypass_actors) ? ruleset.bypass_actors : [];
    const alreadyPresent = actorId !== undefined && bypassActors.some((actor) => {
      return (
        actor.actor_type === actorType &&
        normalizeActorId(actor.actor_id) === actorId &&
        actor.bypass_mode === bypassMode
      );
    });

    if (alreadyPresent) {
      log('ok', `${prefix} ruleset=${ruleset.id} bypass already present`);
      continue;
    }

    if (actorId === undefined) {
      log('dry-run', `${prefix} would inspect/update ruleset=${ruleset.id} name="${ruleset.name}" but no --actor-id was provided`);
      continue;
    }

    const updatedRuleset = sanitizeRulesetForUpdate(ruleset);
    updatedRuleset.bypass_actors = [
      ...bypassActors.map((actor) => ({
        actor_id: normalizeActorId(actor.actor_id),
        actor_type: actor.actor_type,
        bypass_mode: actor.bypass_mode,
      })),
      {
        actor_id: actorId,
        actor_type: actorType,
        bypass_mode: bypassMode,
      },
    ];

    if (!apply) {
      log('dry-run', `${prefix} would update ruleset=${ruleset.id} name="${ruleset.name}"`);
      repoChanged = true;
      continue;
    }

    await gh('PUT', `/repos/${repo.full_name}/rulesets/${ruleset.id}`, updatedRuleset);
    log('changed', `${prefix} updated ruleset=${ruleset.id} name="${ruleset.name}"`);
    repoChanged = true;
  }

  if (!repoMatched) {
    log('skip', `${prefix} no ruleset applies to ${branch}`);
    return 'skipped';
  }

  return repoChanged ? 'changed' : 'unchanged';
}

function rulesetAppliesToBranch(ruleset, branchName) {
  const refName = ruleset.conditions && ruleset.conditions.ref_name;
  if (!refName) return true;

  const includes = refName.include || refName.includes || [];
  const excludes = refName.exclude || refName.excludes || [];
  const ref = `refs/heads/${branchName}`;

  const included = includes.length === 0 || includes.some((pattern) => refPatternMatches(pattern, ref, branchName));
  const excluded = excludes.some((pattern) => refPatternMatches(pattern, ref, branchName));
  return included && !excluded;
}

function refPatternMatches(pattern, ref, branchName) {
  if (pattern === '~ALL') return true;
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
      'User-Agent': 'ndcmsl-ruleset-bypass-script',
      'X-GitHub-Api-Version': API_VERSION,
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const message = data && data.message ? data.message : response.statusText;
    throw new Error(`${method} ${apiPath} failed: ${response.status} ${message}`);
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
      if (!next || next.startsWith('--')) {
        throw new Error(`Missing value for ${arg}`);
      }
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
  node scripts/github-ruleset-bypass.js [options]

Default mode is dry-run. Add --apply to update repository rulesets.

Options:
  --org <org>                 GitHub org. Default: ${DEFAULT_ORG}
  --branch <branch>           Branch to match. Default: ${DEFAULT_BRANCH}
  --actor-type <type>         Ruleset actor type. Default: ${DEFAULT_ACTOR_TYPE}
  --actor-id <id|null>        Ruleset actor id to add. Required with --apply.
  --bypass-mode <mode>        Ruleset bypass mode. Default: ${DEFAULT_BYPASS_MODE}
  --repo <owner/name|name>    Limit to one repo. Can be repeated.
  --include-archived          Include archived repositories.
  --npmrc <path>              Read token from a specific .npmrc file.
  --apply                     Apply changes. Without this, only logs what would change.

Token lookup:
  1. GH_ADMIN_TOKEN
  2. GH_TOKEN
  3. GITHUB_TOKEN
  4. ~/.npmrc _authToken

Examples:
  node scripts/github-ruleset-bypass.js
  node scripts/github-ruleset-bypass.js --repo ndcmsl/erp.wms
  node scripts/github-ruleset-bypass.js --apply
`);
}
