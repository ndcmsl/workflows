const EMOJIS = {
  PKT: "\uD83D\uDE80",
  "PKT-CentOS-9": "\uD83D\uDE80",
  INT: "\uD83D\uDEE0",
  "BEAST-CentOS-9": "\uD83D\uDEE0",
};

function getInput(name, options = {}) {
  const key = `INPUT_${name.replace(/ /g, "_").replace(/-/g, "_").toUpperCase()}`;
  const rawValue = process.env[key] ?? "";
  const value = options.trimWhitespace === false ? rawValue : rawValue.trim();

  if (options.required && !value) {
    throw new Error(`Input required and not supplied: ${name}`);
  }

  return value;
}

function appendFileLine(path, value) {
  require("fs").appendFileSync(path, `${value}\n`, { encoding: "utf8" });
}

function setOutput(name, value) {
  const outputPath = process.env.GITHUB_OUTPUT;
  if (!outputPath) {
    console.log(`::set-output name=${name}::${value}`);
    return;
  }

  const serialized = String(value);
  const delimiter = `codex_${name}_${Date.now()}`;
  appendFileLine(outputPath, `${name}<<${delimiter}`);
  appendFileLine(outputPath, serialized);
  appendFileLine(outputPath, delimiter);
}

function setFailed(error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error(message);
  process.exitCode = 1;
}

function createMessage(text, senderName, senderImage, title) {
  return {
    text: text || "",
    bot: {
      name: senderName || "",
      image: senderImage || "",
    },
    card: {
      title: title || "",
    },
  };
}

function createText({ mode, version, ref, infrastructure, repository, title }) {
  const target = ref === "main" ? version : ref;
  const emoji = infrastructure ? ` ${EMOJIS[infrastructure] || ""}`.trimEnd() : "";
  const releaseTitle = title || (repository.includes("/") ? repository.split("/")[1] : repository);
  const releaseLink = repository && version
    ? `https://github.com/${repository}/releases/tag/${version}`
    : "";

  const texts = {
    release: releaseLink
      ? `Cambios release [${version}](${releaseLink})`
      : `Cambios release ${version || ""}`.trim(),
    deploy: `Deployed ${target || ""} in ${infrastructure || ""}${emoji}`.trim(),
    rollback: `Rollback ${version || ""} in ${infrastructure || ""}${emoji}`.trim(),
    setup: `Setup ${target || ""} in ${infrastructure || ""}${emoji}`.trim(),
  };

  if (!texts[mode]) {
    throw new Error(`Unsupported mode: ${mode || "<empty>"}`);
  }

  if (mode === "release" && !releaseTitle) {
    throw new Error("Unable to build release message without a repository or title");
  }

  return texts[mode];
}

async function sendMessage({ webhook, token, message }) {
  const url = new URL(webhook);
  url.searchParams.set("zapikey", token);

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(message),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Cliq request failed with ${response.status}: ${body}`);
  }
}

async function run() {
  const webhook = getInput("webhook", { required: true });
  const token = getInput("token", { required: true });
  const senderName = getInput("sender-name");
  const senderImage = getInput("sender-image");
  const title = getInput("title");
  const repository = getInput("repository") || process.env.GITHUB_REPOSITORY || "";
  const mode = getInput("mode");
  const infrastructure = getInput("infrastructure");
  const version = getInput("version");
  const ref = getInput("ref") || "main";

  const text = createText({
    mode,
    version,
    ref,
    infrastructure,
    repository,
    title,
  });
  const fallbackTitle = title || (repository.includes("/") ? repository.split("/")[1] : repository);
  const message = createMessage(text, senderName, senderImage, fallbackTitle);

  await sendMessage({ webhook, token, message });
  setOutput("message-json", JSON.stringify(message));
}

run().catch(setFailed);
