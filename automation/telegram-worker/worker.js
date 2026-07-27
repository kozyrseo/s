/**
 * KOZYR — Telegram → GitHub webhook (Cloudflare Worker)
 * ---------------------------------------------------------------------------
 * Адаптировано из рабочего Worker'а PokerNet. Ловит нажатия inline-кнопок под
 * превью статьи и запускает соответствующий workflow в GitHub Actions через
 * workflow_dispatch API. Своего сервера держать не надо.
 *
 * Callback-схема (совпадает с automation/generate.py):
 *   publish:{slug}     → publish-article.yml  (input: slug)
 *   regenerate:{slug}  → generate-article.yml (перегенерация)
 *   reject:{slug}      → reject-article.yml    (архивирует _pending, опционально)
 *   fulltext:{slug}    → читает _pending/{slug}/body.md и присылает в чат
 *
 * Секреты Worker (Cloudflare → Settings → Variables and Secrets):
 *   TELEGRAM_BOT_TOKEN      — токен бота от @BotFather
 *   TELEGRAM_CHAT_ID        — id чата/канала, где разрешены кнопки (защита)
 *   TELEGRAM_SECRET_TOKEN   — секрет вебхука (тот же, что в setWebhook)
 *   GITHUB_TOKEN            — fine-grained PAT, Actions: read/write на репо
 *   GITHUB_REPO             — "kozyrseo/s"
 * ---------------------------------------------------------------------------
 */

const ACTIONS = {
  publish: {
    workflow: "publish-article.yml",
    label: "Публикуется",
    inputs: (slug) => ({ slug }),
    validate: (slug) => /^[a-z0-9-]+$/.test(slug),
  },
  regenerate: {
    workflow: "generate-article.yml",
    label: "Перегенерируется",
    // generate-article.yml принимает topic_file; при перегенерации берём тему по slug.
    inputs: (slug) => ({ topic_file: `automation/topics/${slug}.json` }),
    validate: (slug) => /^[a-z0-9-]+$/.test(slug),
  },
  reject: {
    workflow: "reject-article.yml",
    label: "Отклоняется",
    inputs: (slug) => ({ slug }),
    validate: (slug) => /^[a-z0-9-]+$/.test(slug),
  },
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET") {
      return new Response("KOZYR Telegram webhook is running.", { status: 200 });
    }

    if (request.method === "POST" && url.pathname === "/webhook") {
      const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
      if (secret !== env.TELEGRAM_SECRET_TOKEN) {
        return new Response("Forbidden", { status: 403 });
      }
      let update;
      try {
        update = await request.json();
      } catch (e) {
        return new Response("Bad JSON", { status: 400 });
      }
      if (update.callback_query) {
        ctx.waitUntil(handleCallback(update.callback_query, env));
      }
      return new Response("ok", { status: 200 });
    }

    return new Response("Not found", { status: 404 });
  },
};

async function handleCallback(cb, env) {
  const callbackChatId = String(cb.message?.chat?.id || "");
  const allowedChatId = String(env.TELEGRAM_CHAT_ID || "");
  if (callbackChatId !== allowedChatId) {
    await answerCallback(cb.id, env, "⛔️ Доступ запрещён");
    return;
  }

  const data = cb.data || "";
  const [action, slug] = data.split(":", 2);
  if (!action || !slug) {
    await answerCallback(cb.id, env, "⚠️ Некорректные данные");
    return;
  }

  // fulltext — read-only: читаем body.md из репозитория и шлём в чат.
  if (action === "fulltext") {
    if (!/^[a-z0-9-]+$/.test(slug)) {
      await answerCallback(cb.id, env, "⚠️ Некорректный slug");
      return;
    }
    await answerCallback(cb.id, env, "📄 Готовлю текст...");
    await sendFullText(slug, cb.message, env);
    return;
  }

  const cfg = ACTIONS[action];
  if (!cfg) {
    await answerCallback(cb.id, env, "⚠️ Неизвестное действие");
    return;
  }
  if (cfg.validate && !cfg.validate(slug)) {
    await answerCallback(cb.id, env, "⚠️ Некорректные параметры");
    return;
  }

  const triggered = await triggerWorkflow(cfg.workflow, cfg.inputs(slug), env);
  if (!triggered) {
    await answerCallback(cb.id, env, "❌ Ошибка запуска workflow");
    return;
  }

  await answerCallback(cb.id, env, `⏳ ${cfg.label}...`);
  await editMessageRemoveButtons(cb.message, env, action);
}

async function triggerWorkflow(workflowFile, inputs, env) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflowFile}/dispatches`;
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kozyr-telegram-bot/1.0",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs }),
    });
    if (resp.status === 204) return true;
    console.error(`GitHub dispatch failed (${resp.status}): ${await resp.text()}`);
    return false;
  } catch (e) {
    console.error("triggerWorkflow exception:", e);
    return false;
  }
}

async function answerCallback(callbackId, env, text) {
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ callback_query_id: callbackId, text, show_alert: false }),
    });
  } catch (e) {
    console.error("answerCallback failed:", e);
  }
}

async function editMessageRemoveButtons(message, env, action) {
  if (!message) return;
  const statusLine = {
    publish: "\n\n⏳ *Публикуется...*",
    regenerate: "\n\n🔄 *Перегенерация запущена*",
    reject: "\n\n❌ *Отклонено*",
  }[action] || "";
  const newText = ((message.text || "") + statusLine).slice(0, 4000);
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/editMessageText`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: message.chat?.id,
        message_id: message.message_id,
        text: newText,
        parse_mode: "Markdown",
        disable_web_page_preview: true,
      }),
    });
  } catch (e) {
    console.error("editMessageText failed:", e);
  }
}

async function sendFullText(slug, message, env) {
  const chatId = message?.chat?.id;
  if (!chatId) return;
  const rawUrl = `https://raw.githubusercontent.com/${env.GITHUB_REPO}/main/_pending/${slug}/body.md`;
  let body = null;
  try {
    const resp = await fetch(rawUrl, {
      headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, "User-Agent": "kozyr-telegram-bot/1.0" },
    });
    if (resp.ok) body = await resp.text();
  } catch (e) {
    console.error("sendFullText fetch failed:", e);
  }
  if (body === null) {
    await sendTextMessage(chatId, env, "⚠️ Не удалось загрузить текст статьи");
    return;
  }
  const CHUNK = 3800;
  const total = Math.ceil(body.length / CHUNK);
  for (let i = 0; i < total; i++) {
    const part = body.slice(i * CHUNK, (i + 1) * CHUNK);
    const header = total > 1 ? `📄 *Полный текст* (${i + 1}/${total})\n\n` : "📄 *Полный текст*\n\n";
    await sendTextMessage(chatId, env, header + part, true);
  }
}

async function sendTextMessage(chatId, env, text, plain = false) {
  const payload = { chat_id: chatId, text, disable_web_page_preview: true };
  if (!plain) payload.parse_mode = "Markdown";
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error("sendTextMessage failed:", e);
  }
}
