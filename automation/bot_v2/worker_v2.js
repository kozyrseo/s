/**
 * KOZYR bot v2 — расширенный Telegram → GitHub webhook (Cloudflare Worker)
 * ---------------------------------------------------------------------------
 * Что делает по сравнению с v1:
 *   1. Понимает команды: /help, /generate, /suggested, /queue, /analytics,
 *      /research, /status, /history, /edit, /ab, /cancel.
 *   2. Обрабатывает inline-кнопки под превью статьи и под списками тем.
 *   3. Держит сессии редактирования (когда оператор пишет "новое значение"
 *      в чат после команды /edit — это применяется к meta.json в _pending/).
 *   4. Умеет запускать анализ ключей и присылать отчёт (/research, /analytics).
 *   5. Умеет пропускать статью в очередь (/approve N) без правки таблицы вручную.
 *
 * Callback-схема (обратная совместимость с v1 сохранена):
 *   publish:{slug}         → publish-article.yml
 *   regenerate:{slug}      → generate-multilang.yml (перегенерит все языки темы)
 *   reject:{slug}          → reject-article.yml
 *   fulltext:{slug}        → присылает body.md в чат
 *   sources:{slug}         → присылает meta.json + логи генерации (v2)
 *   edit:{slug}:{field}    → открывает сессию редактирования (v2)
 *   approve_topic:{row}    → status=suggested → queued в таблице (v2)
 *   reject_topic:{row}     → status=rejected (v2)
 *   gen_topic:{row}        → сразу запустить генерацию по теме N (v2)
 *   more_suggested:{page}  → пагинация в /suggested (v2)
 *   ab_start:{slug}        → предложить создать A/B тест (v2)
 *
 * Секреты Worker (Cloudflare → Settings → Variables and Secrets):
 *   TELEGRAM_BOT_TOKEN       — токен бота от @BotFather
 *   TELEGRAM_CHAT_ID         — id чата/канала, где разрешены команды и кнопки
 *   TELEGRAM_SECRET_TOKEN    — секрет вебхука (тот же, что в setWebhook)
 *   GITHUB_TOKEN             — fine-grained PAT: Actions RW + Contents RW
 *   GITHUB_REPO              — "kozyrseo/s"
 * ---------------------------------------------------------------------------
 */

// ==== Действия по кнопкам (сохраняется старый набор + расширяем) ====

const ACTIONS = {
  publish: {
    workflow: "publish-article.yml",
    label: "Публикуется",
    inputs: (slug) => ({ slug }),
    validate: (slug) => /^[a-z0-9-]+$/.test(slug),
  },
  // v2 multilang: publish_all → рендерит все языки этой темы одновременно
  publish_all: {
    workflow: "publish-multilang.yml",
    label: "Публикую все языки",
    inputs: (slug) => ({ slug }),
    validate: (slug) => /^[a-z0-9-]+$/.test(slug),
  },
  regenerate: {
    workflow: "generate-multilang.yml",
    label: "Перегенерируется (все языки)",
    inputs: (slug) => ({ topic_file: `automation/topics/${slug}.json`, country: "", langs: "" }),
    validate: (slug) => /^[a-z0-9-]+$/.test(slug),
  },
  reject: {
    workflow: "reject-article.yml",
    label: "Отклоняется",
    inputs: (slug) => ({ slug }),
    validate: (slug) => /^[a-z0-9-]+$/.test(slug),
  },
  // v2 multilang: reject_all → отклоняет все языки этой темы
  reject_all: {
    workflow: "reject-article.yml",
    label: "Отклоняю все языки",
    inputs: (slug) => ({ slug, all_langs: "true" }),
    validate: (slug) => /^[a-z0-9-]+$/.test(slug),
  },
};

// ==== Точка входа ====

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET") {
      return new Response("KOZYR Telegram bot v2 is running.", { status: 200 });
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

      // Обрабатываем асинхронно, но отвечаем Telegram сразу, чтобы он не ретраил.
      if (update.callback_query) {
        ctx.waitUntil(handleCallback(update.callback_query, env));
      } else if (update.message) {
        ctx.waitUntil(handleMessage(update.message, env));
      }
      return new Response("ok", { status: 200 });
    }

    return new Response("Not found", { status: 404 });
  },
};

// ==== Общий guard: чат в allowlist ====

function isAllowedChat(update, env) {
  const gotChatId = String(update?.chat?.id || update?.message?.chat?.id || "");
  const allowedChatId = String(env.TELEGRAM_CHAT_ID || "");
  return gotChatId === allowedChatId;
}

// ==== Обработка текстовых сообщений (команды + ответы на /edit) ====

async function handleMessage(message, env) {
  if (!isAllowedChat(message, env)) {
    // Не отвечаем совсем — чтобы не раскрывать наличие бота.
    return;
  }
  const chatId = message.chat.id;
  const text = (message.text || "").trim();

  // Логотип партнёра: фото или картинка-документ во время партнёрской сессии.
  if (message.photo || message.document) {
    const ps = await getPartnerSession(chatId, env);
    if (ps) { await handlePartnerLogo(chatId, message, env); return; }
    return; // фото вне партнёрской сессии — игнорируем
  }

  // Пустое сообщение (стикер и т.п.) — игнор.
  if (!text) return;

  // v2 multilang UI: перехватываем нажатия постоянной клавиатуры.
  // Кнопки отправляют текст типа "📝 Темы" — мапим на команду.
  if (BUTTON_TO_COMMAND[text]) {
    const mapped = BUTTON_TO_COMMAND[text];
    if (mapped === "__more_menu__") {
      await showMoreMenu(chatId, env);
      return;
    }
    // Заменяем text на команду и продолжаем как обычно
    message.text = mapped;
    const fakeParts = mapped.split(/\s+/);
    const fakeCmd = fakeParts[0].toLowerCase();
    const fakeArgs = fakeParts.slice(1);
    const handler = getCommandHandler(fakeCmd);
    if (handler) {
      await handler(chatId, fakeArgs, message, env);
    }
    return;
  }

  // 1. Если открыта какая-то сессия — это ответ на неё
  //    (кроме случая когда пришла команда, начинающаяся с /).
  if (!text.startsWith("/")) {
    // 1a. Сессия добавления партнёра (анкета) — ПРИОРИТЕТ.
    //     handlePartnerAnswer сам разберётся со стадией (await_text /
    //     await_more / parsing / await_confirm). Свободный текст в любой
    //     из этих стадий = ввод или дополнение анкеты.
    const partnerSession = await getPartnerSession(chatId, env);
    if (partnerSession) {
      await handlePartnerAnswer(chatId, text, message, env);
      return;
    }
    // 1b. Сессия правки meta.json статьи.
    const session = await getOpenEditSessionForChat(chatId, env);
    if (session) {
      await applyEditFromMessage(session, text, message, env);
      return;
    }
    // Просто болтовня — молча игнорируем.
    return;
  }

  // 2. Разбор команды. Формат: /cmd arg1 arg2 ...
  const parts = text.split(/\s+/);
  const cmd = parts[0].toLowerCase().split("@")[0];   // /generate@bot → /generate
  const args = parts.slice(1);

  const handler = getCommandHandler(cmd);
  if (!handler) {
    await sendMessage(chatId, env, `⚠️ Неизвестная команда \`${escapeMd(cmd)}\`. Попробуй /help`);
    return;
  }
  try {
    await handler(chatId, args, message, env);
  } catch (e) {
    console.error(`Handler ${cmd} failed:`, e);
    await sendMessage(chatId, env, `❌ Ошибка выполнения: ${escapeMd(String(e).slice(0, 300))}`);
  }
}

// Возвращает функцию-обработчик команды. Вынесено отдельно чтобы можно было
// вызывать из перехватчика reply-кнопок постоянной клавиатуры.
function getCommandHandler(cmd) {
  const handlers = {
    "/start":       cmdHelp,
    "/help":        cmdHelp,
    "/generate":    cmdGenerate,
    "/suggested":   cmdSuggested,
    "/queue":       cmdQueue,
    "/approve":     cmdApprove,
    "/rejectrow":   cmdRejectRow,
    "/research":    cmdResearch,
    "/analytics":   cmdAnalytics,
    "/status":      cmdStatus,
    "/history":     cmdHistory,
    "/edit":        cmdEdit,
    "/ab":          cmdAb,
    "/cancel":      cmdCancel,
    "/pending":     cmdPending,
    "/countries":   cmdCountries,
    "/translate":   cmdTranslate,
    "/menu":        cmdMenu,
    "/addpartner":  cmdAddPartner,
    "/newpartner":  cmdAddPartner,   // алиас
    "/partner":     cmdAddPartner,   // алиас
  };
  return handlers[cmd] || null;
}

// v2 multilang UI: показ inline-меню "⚙️ Ещё" — редкие команды
async function showMoreMenu(chatId, env) {
  const kb = [
    [{ text: "➕ Добавить партнёра", callback_data: "menu_action:add_partner" }],
    [{ text: "🔍 Найти новые темы", callback_data: "menu_action:research" }],
    [{ text: "🔄 Обновить список тем", callback_data: "menu_action:refresh" }],
    [{ text: "📊 Аналитика: выбрать период", callback_data: "menu_action:analytics_period" }],
    [{ text: "📈 Статус пайплайна", callback_data: "menu_action:status" }],
    [{ text: "📋 Очередь тем", callback_data: "menu_action:queue" }],
    [{ text: "🌐 Перевести статью…", callback_data: "menu_action:translate_prompt" }],
    [{ text: "🎯 A/B тесты…", callback_data: "menu_action:ab_prompt" }],
    [{ text: "❓ Справка", callback_data: "menu_action:help" }],
  ];
  await sendMessage(chatId, env,
    "*⚙️ Дополнительные действия*\n\nВыбери что нужно:",
    kb);
}

// Команда /menu — перерисовать главную клавиатуру, если она пропала
async function cmdMenu(chatId, args, msg, env) {
  await sendMessage(chatId, env,
    "🎛 *Главное меню*\n\nКнопки снизу всегда доступны. Нажми любую или используй команды напрямую (`/help`).",
    null,
    MAIN_MENU_KEYBOARD,
  );
}

// ==== Обработка нажатий на inline-кнопки ====

async function handleCallback(cb, env) {
  const callbackChatId = String(cb.message?.chat?.id || "");
  const allowedChatId = String(env.TELEGRAM_CHAT_ID || "");
  if (callbackChatId !== allowedChatId) {
    await answerCallback(cb.id, env, "⛔️ Доступ запрещён");
    return;
  }

  const data = cb.data || "";
  // Мы допускаем 2-3 сегмента: action:slug или action:slug:field или action:row
  const segments = data.split(":");
  const action = segments[0];

  // Простые "просмотр"-действия
  if (action === "fulltext") {
    const slug = segments[1];
    if (!slug || !/^[a-z0-9-]+$/.test(slug)) {
      await answerCallback(cb.id, env, "⚠️ Некорректный slug");
      return;
    }
    await answerCallback(cb.id, env, "📄 Готовлю текст...");
    await sendFullText(slug, cb.message, env);
    return;
  }

  if (action === "sources") {
    const slug = segments[1];
    if (!slug || !/^[a-z0-9-]+$/.test(slug)) {
      await answerCallback(cb.id, env, "⚠️ Некорректный slug");
      return;
    }
    await answerCallback(cb.id, env, "🧾 Готовлю исходники...");
    await sendSources(slug, cb.message, env);
    return;
  }

  // v2 multilang: fulltext_lang:{lang}:{slug}
  if (action === "fulltext_lang") {
    const lang = segments[1];
    const slug = segments[2];
    if (!lang || !slug || !/^[a-z0-9-]+$/.test(slug) || !/^[a-z]{2}$/.test(lang)) {
      await answerCallback(cb.id, env, "⚠️ Некорректные данные");
      return;
    }
    await answerCallback(cb.id, env, `📄 ${lang.toUpperCase()}...`);
    await sendFullTextForLang(slug, lang, cb.message, env);
    return;
  }

  // v2 multilang: edit_menu_lang:{lang}:{slug} — меню правки для конкретной языковой версии
  if (action === "edit_menu_lang") {
    const lang = segments[1];
    const slug = segments[2];
    if (!lang || !slug) {
      await answerCallback(cb.id, env, "⚠️ Некорректные данные");
      return;
    }
    await answerCallback(cb.id, env, "");
    await showEditFieldMenuForLang(slug, lang, cb.message, env);
    return;
  }

  // v2 multilang: edit_lang:{lang}:{slug}:{field}
  if (action === "edit_lang") {
    const lang = segments[1];
    const slug = segments[2];
    const field = segments[3];
    if (!lang || !slug || !field) {
      await answerCallback(cb.id, env, "⚠️ Некорректные данные");
      return;
    }
    await answerCallback(cb.id, env, "✏️ Открываю редактор...");
    await startEditSessionForLang(slug, lang, field, cb.message, env);
    return;
  }

  if (action === "edit") {
    // edit:{slug}:{field}
    const slug = segments[1];
    const field = segments[2];
    if (!slug || !field) {
      await answerCallback(cb.id, env, "⚠️ Некорректные данные");
      return;
    }
    await answerCallback(cb.id, env, "✏️ Открываю редактор...");
    await startEditSession(slug, field, cb.message, env);
    return;
  }

  if (action === "edit_menu") {
    // edit_menu:{slug} — показать список полей на выбор
    const slug = segments[1];
    if (!slug) {
      await answerCallback(cb.id, env, "⚠️ Нет slug");
      return;
    }
    await answerCallback(cb.id, env, "");
    await showEditFieldMenu(slug, cb.message, env);
    return;
  }

  // === Suggested topics: одобрить/отклонить/сразу генерить ===
  if (action === "approve_topic" || action === "reject_topic" || action === "gen_topic") {
    const row = parseInt(segments[1], 10);
    if (!Number.isFinite(row) || row < 2) {
      await answerCallback(cb.id, env, "⚠️ Некорректный номер строки");
      return;
    }
    await handleSuggestedAction(action, row, cb, env);
    return;
  }

  // ═══════════════════════════════════════════════════════════════════
  //  ПАРТНЁРЫ — единый блок обработки (вынесен в handlePartnerCallback,
  //  чтобы держать всю партнёрскую логику в одном месте).
  //  Схема callback_data (совпадает с automation/parse_partner.py):
  //    pcountry:{id}:{cc}  → выбрать основную страну, если парсер не понял
  //    pconfirm:{id}       → generate-partner-tpl.yml (preview) — собрать страницу (шаблон)
  //    pmore:{id}          → перевести сессию в режим «дополнить текстом»
  //    pcancel             → отменить сессию + удалить черновик
  //    ppublish:{id}       → generate-partner-tpl.yml (publish=true) — в прод (шаблон)
  //    pshow:{id}          → показать сводку по конкретному черновику
  //    pshow_latest        → показать последний черновик (если авто-сводка не дошла)
  // ═══════════════════════════════════════════════════════════════════
  if (action === "pcountry" || action === "pconfirm" || action === "pmore" ||
      action === "pcancel" || action === "ppublish" ||
      action === "pshow" || action === "pshow_latest" ||
      action === "pedit" || action === "pfield" || action === "pacc") {
    await handlePartnerCallback(action, segments, cb, env);
    return;
  }

  // === Пагинация /suggested ===
  if (action === "more_suggested") {
    const page = parseInt(segments[1] || "0", 10);
    await answerCallback(cb.id, env, "");
    await cmdSuggested(cb.message.chat.id, [String(page)], cb.message, env);
    return;
  }

  // === Выбор периода аналитики кнопкой ===
  // an_period:{default|week|month|quarter|year}
  if (action === "an_period") {
    const period = segments[1] || "default";
    await answerCallback(cb.id, env, "📊 Запускаю сбор...");
    await runAnalytics(cb.message.chat.id, period, env);
    return;
  }

  // === Полный markdown-отчёт по кнопке (без повторного сбора) ===
  if (action === "an_full") {
    await answerCallback(cb.id, env, "📄 Загружаю отчёт...");
    await sendFullReport(cb.message.chat.id, env);
    return;
  }

  // === Обновить мгновенную сводку (перечитать report.json) ===
  if (action === "an_refresh") {
    await answerCallback(cb.id, env, "🔄 Обновляю...");
    await showAnalyticsDashboard(cb.message.chat.id, env);
    return;
  }

  // === Разделы дашборда (каждый — НОВОЕ сообщение, история сохраняется) ===
  // an_section:{articles|partners|dynamics|queries|traffic|ga4}
  if (action === "an_section") {
    const which = segments[1] || "articles";
    await answerCallback(cb.id, env, "📂 Открываю раздел...");
    await sendAnalyticsSection(cb.message.chat.id, which, env);
    return;
  }

  // v2 multilang UI: menu_action от кнопок "⚙️ Ещё"
  if (action === "menu_action") {
    const which = segments[1];
    await answerCallback(cb.id, env, "");
    const chatId = cb.message.chat.id;
    if (which === "add_partner") {
      await cmdAddPartner(chatId, [], cb.message, env);
    } else if (which === "research") {
      await cmdResearch(chatId, [], cb.message, env);
    } else if (which === "refresh") {
      const ok = await triggerWorkflow("refresh-suggested.yml", {}, env);
      await sendMessage(chatId, env, ok
        ? "🔄 Обновляю список тем. Через ~1 минуту нажми «📝 Темы»."
        : "❌ Не удалось запустить refresh-suggested.yml.");
    } else if (which === "analytics_period") {
      // Показать кнопки выбора периода (как «📊 Аналитика» из главного меню)
      await cmdAnalytics(chatId, [], cb.message, env);
    } else if (which === "status") {
      await cmdStatus(chatId, [], cb.message, env);
    } else if (which === "queue") {
      await cmdQueue(chatId, [], cb.message, env);
    } else if (which === "help") {
      await cmdHelp(chatId, [], cb.message, env);
    } else if (which === "translate_prompt") {
      await sendMessage(chatId, env,
        "🌐 *Перевод статьи*\n\n" +
        "Пришли команду в формате:\n" +
        "`/translate slug uk`\n\n" +
        "Где:\n" +
        "  • `slug` — идентификатор статьи (папка в `_pending/` или `ua/blog/`)\n" +
        "  • `uk` — язык, на который переводить\n\n" +
        "Пример: `/translate chto-takoe-reykbek uk`");
    } else if (which === "ab_prompt") {
      await sendMessage(chatId, env,
        "🎯 *A/B тест*\n\n" +
        "Пришли команду: `/ab slug`\n\n" +
        "Пример: `/ab kak-vybrat-rum`");
    }
    return;
  }

  // v2 multilang UI: точечная публикация одного языка
  // publish_lang:{lang}:{slug}
  if (action === "publish_lang") {
    const lang = segments[1];
    const slug = segments[2];
    if (!lang || !slug) {
      await answerCallback(cb.id, env, "⚠️ Некорректные данные");
      return;
    }
    await answerCallback(cb.id, env, `🚀 Публикую ${lang.toUpperCase()}...`);
    const ok = await triggerWorkflow("publish-multilang.yml",
      { slug, langs: lang }, env);
    await sendMessage(cb.message.chat.id, env, ok
      ? `🚀 Публикую только *${lang.toUpperCase()}* — \`${escapeMd(slug)}\`. Через 1-2 мин будет готово.`
      : `❌ Не удалось запустить publish-multilang для ${lang}`);
    return;
  }

  // === Основные действия v1: publish / regenerate / reject ===
  const cfg = ACTIONS[action];
  if (!cfg) {
    await answerCallback(cb.id, env, "⚠️ Неизвестное действие");
    return;
  }
  const slug = segments[1];
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

  // Логируем в history
  await appendHistory(slug, action, cb.from?.id, {}, env);
}

// ==== Команды ====

async function cmdHelp(chatId, args, msg, env) {
  const text = `🤖 *KOZYR bot v2*

Все действия — через кнопки снизу или прямые команды.

*🎛 Главные кнопки (снизу чата):*
  📝 Темы — предложенные темы (кнопки «в очередь / генерить / отклонить»)
  ⚡ Сгенерить — статья из очереди
  📊 Аналитика — мгновенная сводка + разделы по кнопкам (📄 Статьи · 🎯 Партнёры · 📈 Динамика · 🔎 Запросы · 🌍 Трафик · ⚙️ GA4), выбор периода, полный отчёт
  📋 Pending — статьи на ревью по всем языкам
  🌍 Страны — список настроенных стран и их языков
  ➕ Партнёр — добавить покер-рум/клуб по свободному описанию
  ⚙️ Ещё — research, обновить темы, аналитика за период, статус, translate, A/B

*➕ Добавление партнёра (авто):*
  Нажми «➕ Добавить партнёра» в меню → опиши рум/клуб одним сообщением своими словами → бот распарсит через Claude, покажет «что понял» и кнопки:
    ✅ Создать страницу · ✏️ Дополнить текстом · ❌ Отмена
  Готовая страница появится в \`_pending_partner/{id}/\` — проверь и жми «🌐 Опубликовать».

*🌍 Мульти-язык (автоматически):*
  Одна тема в Sheets = одна страна = все её языки. Указываешь \`country=ua\` в таблице — бот генерит русскую версию и переводит на украинскую, публикует обе одновременно.
  Под превью статьи — кнопки:
    ✅ Опубликовать все · 🚀 Только RU · 🚀 Только UK · ✏️ Правка по языку

*Правка pending-статьи:*
  Открой любое превью или /edit \`slug\`.
  Разрешённые поля: \`meta_title\` · \`meta_description\` · \`h1_title\` · \`image_prompt\` · \`notes\` · \`target_page\`
  Отмена: /cancel

*Основные команды (те же что кнопки):*
  /suggested \`[page]\` · /generate \`[N]\` · /analytics \`[week|month|quarter|year|N]\` · /pending · /countries · /queue · /research · /status · /translate \`slug lang\` · /history \`slug\` · /edit \`slug\` · /addpartner

Каждое действие запускает GitHub Actions — логи в \`Actions\`.`;
  // Отправляем и главную клавиатуру, чтобы она закрепилась у пользователя
  await sendMessage(chatId, env, text, null, MAIN_MENU_KEYBOARD);
}

async function cmdGenerate(chatId, args, msg, env) {
  // /generate       → без аргументов, генератор возьмёт первую queued тему из таблицы
  // /generate N     → генерировать по строке N (dump-topic-file + запуск)
  if (args.length === 0) {
    const ok = await triggerWorkflow("generate-multilang.yml", { country: "", langs: "" }, env);
    await sendMessage(chatId, env, ok
      ? "⏳ Запустил генерацию из очереди (RU + UK). Превью придёт с обоими языками."
      : "❌ Не удалось запустить workflow. Проверь GITHUB_TOKEN.");
    return;
  }
  const row = parseInt(args[0], 10);
  if (!Number.isFinite(row) || row < 2) {
    await sendMessage(chatId, env, "⚠️ Аргумент должен быть номером строки в таблице (≥ 2)");
    return;
  }
  // Запускаем спец-workflow, который сам вычитает строку и сгенерит
  const ok = await triggerWorkflow(
    "generate-from-row.yml",
    { row: String(row) },
    env,
  );
  await sendMessage(chatId, env, ok
    ? `⏳ Запустил генерацию по строке ${row}. Тема будет переведена в processing, потом придёт превью.`
    : "❌ Не удалось запустить workflow.");
}

async function cmdSuggested(chatId, args, msg, env) {
  const page = Math.max(0, parseInt(args[0] || "0", 10));
  const PAGE_SIZE = 5;

  // --- ШАГ 1: убрать за собой прошлые сообщения /suggested ---
  // При открытии ПЕРВОЙ страницы (page 0) чистим весь предыдущий список,
  // чтобы в чате не копились десятки старых карточек с мёртвыми кнопками.
  // id прошлых сообщений храним в GitHub-файле (KV не настроен).
  const MSG_IDS_PATH = ".bot_state/cache/suggested_msg_ids.json";
  if (page === 0) {
    const prev = await ghReadJSON(MSG_IDS_PATH, env);
    if (prev && Array.isArray(prev.ids) && String(prev.chat_id) === String(chatId)) {
      for (const mid of prev.ids) {
        await deleteMessage(chatId, mid, env);
      }
      await ghWriteFile(MSG_IDS_PATH,
        JSON.stringify({ chat_id: chatId, ids: [] }, null, 2),
        "bot: clear suggested msg ids", env).catch(() => {});
    }
  }

  const suggested = await ghReadJSON(".bot_state/cache/suggested_snapshot.json", env);
  // Если snapshot есть — берём из него (быстрее). Иначе просим Actions его обновить.
  if (!suggested || !Array.isArray(suggested.rows)) {
    await sendMessage(chatId, env, "📥 Список тем ещё не собран. Запускаю обновление...");
    const ok = await triggerWorkflow("refresh-suggested.yml", {}, env);
    if (!ok) {
      await sendMessage(chatId, env, "❌ Не удалось запустить refresh-suggested.yml");
      return;
    }
    await sendMessage(chatId, env, "⏳ Обновление запущено. Повтори /suggested через ~1 минуту.");
    return;
  }

  // --- ШАГ 2: фильтр на актуальность ---
  // Снапшот может отставать от таблицы (напр. только что отклонённые темы
  // ещё числятся здесь до пересборки). Показываем ТОЛЬКО status=suggested —
  // страховка на случай протухшего снапшота.
  const rows = (suggested.rows || []).filter(
    r => String(r.status || "suggested").trim().toLowerCase() === "suggested"
  );
  if (rows.length === 0) {
    await sendMessage(chatId, env,
      "📭 Suggested-тем нет (всё разобрано). Запусти /research чтобы найти новые.");
    return;
  }

  const start = page * PAGE_SIZE;
  const chunk = rows.slice(start, start + PAGE_SIZE);
  if (chunk.length === 0) {
    await sendMessage(chatId, env, "📭 На этой странице тем нет. /suggested 0");
    return;
  }

  // Свежесть снапшота: если старше ~2ч — предупредим и пересоберём.
  let staleNote = "";
  if (suggested.collected_at) {
    const ageMs = Date.now() - new Date(suggested.collected_at).getTime();
    if (ageMs > 2 * 3600 * 1000) {
      staleNote = "\n⏳ _снапшот устарел, обновляю — повтори через минуту_";
      // await, а не fire-and-forget: в Cloudflare Workers фоновый промис без
      // ctx.waitUntil может не выполниться. Один лишний API-вызов не страшен.
      await triggerWorkflow("refresh-suggested.yml", {}, env);
    }
  }

  const collectedAt = suggested.collected_at
    ? `_снапшот от: ${suggested.collected_at}_\n\n`
    : "";
  const header = `📋 *Suggested темы* — стр. ${page + 1} (актуальных ${rows.length})\n${collectedAt}${staleNote}`;

  // Собираем id всех отправленных сообщений — удалим при следующем /suggested.
  const sentIds = [];

  // По одному сообщению на тему, чтобы кнопки удобно ложились
  const hid = await sendMessage(chatId, env, header);
  if (hid) sentIds.push(hid);

  for (const r of chunk) {
    // v2 multilang: показываем страну + языки
    const country = String(r.country || "").trim();
    const langsOverride = String(r.langs || "").trim();
    const countryLine = country
      ? `🌍 Страна: \`${escapeMd(country)}\`` +
        (langsOverride ? ` · языки: \`${escapeMd(langsOverride)}\`` : ` · все языки страны`)
      : `⚠️ Country не задан (legacy тема)`;

    const text = `*Строка ${r._row}* · ${escapeMd(r.topic || "(без темы)")}\n\n` +
      `🎯 \`${escapeMd(r.primary_keyword || "")}\`\n` +
      `📍 ${escapeMd(r.target_page || "")} · ${escapeMd(r.intent || "informational")}\n` +
      `${countryLine}\n` +
      (r.evidence ? `💡 ${escapeMd(String(r.evidence).slice(0, 300))}\n` : "") +
      (r.notes ? `📝 ${escapeMd(String(r.notes).slice(0, 300))}` : "");
    const kb = [
      [
        { text: "✅ В очередь", callback_data: `approve_topic:${r._row}` },
        { text: "⚡ Генерить", callback_data: `gen_topic:${r._row}` },
      ],
      [
        { text: "❌ Отклонить", callback_data: `reject_topic:${r._row}` },
      ],
    ];
    const mid = await sendMessage(chatId, env, text, kb);
    if (mid) sentIds.push(mid);
  }

  // Пагинация
  if (start + PAGE_SIZE < rows.length) {
    const pid = await sendMessage(chatId, env,
      `Ещё ${rows.length - (start + PAGE_SIZE)} тем впереди.`,
      [[{ text: "→ Следующая страница", callback_data: `more_suggested:${page + 1}` }]]);
    if (pid) sentIds.push(pid);
  }

  // --- ШАГ 3: запомнить id этой партии для удаления при след. /suggested ---
  // На page 0 перезаписываем; на последующих страницах — дополняем.
  let idsToSave = sentIds;
  if (page > 0) {
    const existing = await ghReadJSON(MSG_IDS_PATH, env);
    if (existing && Array.isArray(existing.ids) && String(existing.chat_id) === String(chatId)) {
      idsToSave = existing.ids.concat(sentIds);
    }
  }
  await ghWriteFile(MSG_IDS_PATH,
    JSON.stringify({ chat_id: chatId, ids: idsToSave }, null, 2),
    "bot: track suggested msg ids", env).catch(() => {});
}

async function cmdQueue(chatId, args, msg, env) {
  // Читаем snapshot очереди
  const snap = await ghReadJSON(".bot_state/cache/queue_snapshot.json", env);
  if (!snap || !Array.isArray(snap.rows)) {
    await sendMessage(chatId, env, "📥 Snapshot очереди не найден. Запускаю обновление...");
    await triggerWorkflow("refresh-suggested.yml", {}, env);
    await sendMessage(chatId, env, "⏳ Через минуту повтори /queue.");
    return;
  }
  if (snap.rows.length === 0) {
    await sendMessage(chatId, env, "📭 Очередь пуста. /suggested → одобри темы или /research → найди новые.");
    return;
  }
  const lines = [`📋 *В очереди на генерацию: ${snap.rows.length}*`, ""];
  for (const r of snap.rows.slice(0, 15)) {
    lines.push(`• [row ${r._row}] ${escapeMd(String(r.topic || "").slice(0, 80))}`);
  }
  if (snap.rows.length > 15) {
    lines.push(`\n… и ещё ${snap.rows.length - 15}`);
  }
  lines.push("");
  lines.push("Запусти /generate — генератор возьмёт первую строку.");
  await sendMessage(chatId, env, lines.join("\n"));
}

async function cmdApprove(chatId, args, msg, env) {
  const row = parseInt(args[0], 10);
  if (!Number.isFinite(row) || row < 2) {
    await sendMessage(chatId, env, "Использование: `/approve N` — номер строки в таблице");
    return;
  }
  const ok = await triggerWorkflow("approve-topic.yml", { row: String(row) }, env);
  await sendMessage(chatId, env, ok
    ? `✅ Строка ${row} переводится в \`queued\`.`
    : "❌ Не удалось запустить workflow.");
}

async function cmdRejectRow(chatId, args, msg, env) {
  const row = parseInt(args[0], 10);
  if (!Number.isFinite(row) || row < 2) {
    await sendMessage(chatId, env, "Использование: `/rejectrow N`");
    return;
  }
  const ok = await triggerWorkflow("reject-topic-row.yml", { row: String(row) }, env);
  await sendMessage(chatId, env, ok
    ? `❌ Строка ${row} переводится в \`rejected\`.`
    : "❌ Не удалось запустить workflow.");
}

async function cmdResearch(chatId, args, msg, env) {
  const ok = await triggerWorkflow("research-keywords.yml", {}, env);
  await sendMessage(chatId, env, ok
    ? "🔎 Запустил анализ ключей. Обычно занимает 3-5 минут. Результат придёт сюда."
    : "❌ Не удалось запустить research-keywords.yml.");
}

async function cmdAnalytics(chatId, args, msg, env) {
  // Если период передан аргументом (/analytics month, /analytics 30) —
  // запускаем сразу (обратная совместимость с вводом команды).
  const arg = ((args && args[0]) || "").trim().toLowerCase();
  if (arg) {
    await runAnalytics(chatId, arg, env);
    return;
  }
  // Иначе — показываем ДАШБОРД: мгновенную сводку из последнего отчёта
  // (без ожидания workflow) + кнопки выбора периода для свежего сбора.
  await showAnalyticsDashboard(chatId, env);
}

// Дашборд аналитики: читает последний собранный отчёт (analytics/report.json
// из репозитория) и рисует компактную сводку СРАЗУ, не дожидаясь workflow.
// Под сводкой — кнопки периода (свежий сбор), полный отчёт и обновление.
async function showAnalyticsDashboard(chatId, env) {
  const report = await ghReadJSON("analytics/report.json", env);
  const summary = renderInstantSummary(report);
  const kb = [
    // Разделы дашборда — каждый открывается новым сообщением
    [
      { text: "📄 Статьи", callback_data: "an_section:articles" },
      { text: "🎯 Партнёры", callback_data: "an_section:partners" },
    ],
    [
      { text: "📈 Динамика", callback_data: "an_section:dynamics" },
      { text: "🔎 Запросы", callback_data: "an_section:queries" },
    ],
    [
      { text: "🌍 Трафик", callback_data: "an_section:traffic" },
      { text: "⚙️ GA4-поведение", callback_data: "an_section:ga4" },
    ],
    // Свежий сбор за период
    [{ text: "📊 Собрать за 60 дней", callback_data: "an_period:default" }],
    [
      { text: "🗓 Неделя", callback_data: "an_period:week" },
      { text: "🗓 Месяц", callback_data: "an_period:month" },
      { text: "🗓 Квартал", callback_data: "an_period:quarter" },
    ],
    [
      { text: "📄 Полный отчёт", callback_data: "an_full" },
      { text: "🔄 Обновить", callback_data: "an_refresh" },
    ],
  ];
  await sendMessage(chatId, env, summary, kb);
}

// ═══════════════════════════════════════════════════════════════════
//  РАЗДЕЛЫ ДАШБОРДА — каждый приходит отдельным сообщением
//  Читают analytics/report.json (текущий) и report_prev.json (для динамики).
// ═══════════════════════════════════════════════════════════════════
async function sendAnalyticsSection(chatId, which, env) {
  const report = await ghReadJSON("analytics/report.json", env);
  if (!report || !report.articles) {
    await sendMessage(chatId, env,
      "📊 Отчёта пока нет. Нажми «📊 Собрать за 60 дней», бот соберёт данные.");
    return;
  }
  let text;
  const back = [[{ text: "◀️ К дашборду", callback_data: "an_refresh" }]];
  switch (which) {
    case "articles":  text = renderSectionArticles(report); break;
    case "partners":  text = renderSectionPartners(report); break;
    case "dynamics":  text = await renderSectionDynamics(report, env); break;
    case "queries":   text = renderSectionQueries(report); break;
    case "traffic":   text = renderSectionTraffic(report); break;
    case "ga4":       text = renderSectionGA4(report); break;
    default:          text = "Неизвестный раздел.";
  }
  if (text.length > 3900) text = text.slice(0, 3880) + "\n…(обрезано)";
  await sendMessage(chatId, env, text, back);
}

function _secImpr(a) { return (a.stats && a.stats.impressions) || 0; }
function _secClk(a) { return (a.stats && a.stats.clicks) || 0; }
function _secPos(a) {
  const p = a.stats && a.stats.position;
  return (p === null || p === undefined) ? null : p;
}
function _secRankedByImpr(articles) {
  return articles.slice().sort((a, b) => {
    const di = _secImpr(b) - _secImpr(a);
    if (di) return di;
    return (_secPos(a) ?? 999) - (_secPos(b) ?? 999);
  });
}

// ── 📄 СТАТЬИ: показы / клики / CTR / позиция ──
function renderSectionArticles(report) {
  const articles = report.articles || [];
  const site = report.site || {};
  const ranked = _secRankedByImpr(articles);
  const L = ["📄 *Статьи — показы · клики · CTR · позиция*", ""];

  // Если есть данные по всему сайту — показываем ИХ (полная картина).
  const sitePages = Array.isArray(site.pages) ? site.pages.filter(p => (p.impressions || 0) > 0) : [];
  if (sitePages.length) {
    const st = site.totals || {};
    L.push(`Σ по сайту: показы *${st.impressions || 0}* · клики *${st.clicks || 0}* · CTR *${((st.ctr || 0) * 100).toFixed(2)}%*`);
    L.push(`Страниц с трафиком: *${sitePages.length}*`);
    L.push("");
    L.push("*Все страницы сайта (по показам):*");
    for (const p of sitePages.slice(0, 20)) {
      const pos = (typeof p.position === "number") ? p.position.toFixed(1) : "—";
      const c = (p.ctr || 0) * 100;
      const label = p.title && p.title !== p.path ? p.title : p.path;
      L.push(`• ${escapeMd(String(label).slice(0, 44))}`);
      L.push(`   ${p.impressions || 0} 👁 · ${p.clicks || 0} 🖱 · ${c.toFixed(1)}% · поз. ${pos}`);
    }
    return L.join("\n");
  }

  // Фолбэк: старая логика по 6 статьям taxonomy.
  const sumI = articles.reduce((s, a) => s + _secImpr(a), 0);
  const sumC = articles.reduce((s, a) => s + _secClk(a), 0);
  const ctr = sumI ? (sumC / sumI * 100) : 0;
  L.push(`Σ показы: *${sumI}* · клики: *${sumC}* · CTR *${ctr.toFixed(2)}%*`);
  L.push("");
  for (const a of ranked) {
    const s = a.stats || {};
    const pos = (typeof s.position === "number") ? s.position.toFixed(1) : "—";
    const c = (s.ctr || 0) * 100;
    L.push(`• ${escapeMd(String(a.title || "").slice(0, 44))}`);
    L.push(`   ${s.impressions || 0} 👁 · ${s.clicks || 0} 🖱 · ${c.toFixed(1)}% · поз. ${pos}`);
  }
  return L.join("\n");
}

// ── 🎯 ПАРТНЁРЫ: переходы к PokerBet / KlubOk (по данным GA4-событий) ──
function renderSectionPartners(report) {
  const ga4 = report.ga4 || {};
  const articles = report.articles || [];
  const L = ["🎯 *Переходы к партнёрам*", ""];

  if (!ga4.available) {
    L.push("_GA4 ещё не отдаёт данные (нужны визиты + 24–48ч)._");
    L.push("");
    L.push("Как только пойдёт трафик, здесь появится:");
    L.push("• сколько человек ушло на *каждого* партнёра");
    L.push("• откуда кликнули (виджет / CTA / карточка / панель)");
    L.push("• с какой страницы шёл переход");
    L.push("• внешние переходы (outbound) vs на страницу-обзор");
    return L.join("\n");
  }

  const byPartner = {};   // partner → total (из link_label)
  const bySource = {};    // источник блока → total
  const byEvent = { affiliate_click: 0, partner_page_click: 0 };
  const byPage = {};      // страница → total

  for (const a of articles) {
    const conv = a.conversions || {};
    const total = conv.total || 0;
    if (!total) continue;
    byPage[a.title || a.slug] = (byPage[a.title || a.slug] || 0) + total;
    for (const [src, n] of Object.entries(conv.by_source || {})) {
      bySource[src] = (bySource[src] || 0) + n;
    }
    for (const [ev, n] of Object.entries(conv.by_event || {})) {
      byEvent[ev] = (byEvent[ev] || 0) + n;
    }
  }
  const convByPage = ga4.conversions_by_page || {};
  for (const rec of Object.values(convByPage)) {
    for (const [label, n] of Object.entries(rec.by_label || {})) {
      byPartner[label] = (byPartner[label] || 0) + n;
    }
  }

  const totalClicks = articles.reduce((s, a) => s + ((a.conversions && a.conversions.total) || 0), 0);
  const totalViews = (ga4.totals && ga4.totals.views) || 0;
  const cr = totalViews ? (totalClicks / totalViews * 100) : 0;
  L.push(`Всего переходов: *${totalClicks}* · конверсия *${cr.toFixed(2)}%*`);
  L.push(`Внешние: *${byEvent.affiliate_click || 0}* · на обзор: *${byEvent.partner_page_click || 0}*`);
  L.push("");

  const partners = Object.entries(byPartner).sort((a, b) => b[1] - a[1]);
  if (partners.length) {
    L.push("*По партнёрам:*");
    for (const [p, n] of partners) {
      const share = totalClicks ? (n / totalClicks * 100).toFixed(0) : 0;
      L.push(`• ${escapeMd(p)}: *${n}* (${share}%)`);
    }
    L.push("");
  }

  const sources = Object.entries(bySource).sort((a, b) => b[1] - a[1]);
  if (sources.length) {
    L.push("*Откуда кликают (блок):*");
    for (const [src, n] of sources) {
      L.push(`• ${SECTION_SRC_LABELS[src] || src}: *${n}*`);
    }
    L.push("");
  }

  const pages = Object.entries(byPage).sort((a, b) => b[1] - a[1]).slice(0, 6);
  if (pages.length) {
    L.push("*Страницы, дающие переходы:*");
    for (const [pg, n] of pages) {
      L.push(`• ${escapeMd(String(pg).slice(0, 40))} — *${n}*`);
    }
  }
  return L.join("\n");
}

// ── 📈 ДИНАМИКА: сравнение с прошлым сбором ──
async function renderSectionDynamics(report, env) {
  const prev = await ghReadJSON("analytics/report_prev.json", env);
  const L = ["📈 *Динамика vs прошлый период*", ""];
  if (!prev || !prev.articles) {
    L.push("_Нет предыдущего снимка для сравнения._");
    L.push("");
    L.push("Динамика появится со *второго* сбора: бот сохраняет прошлый");
    L.push("отчёт в `report_prev.json` и сравнивает показы, клики и позиции.");
    return L.join("\n");
  }

  const cur = report.articles || [];
  const old = prev.articles || [];
  const oldBySlug = {};
  for (const a of old) oldBySlug[a.slug] = a;

  const sumI = cur.reduce((s, a) => s + _secImpr(a), 0);
  const sumIold = old.reduce((s, a) => s + _secImpr(a), 0);
  const sumC = cur.reduce((s, a) => s + _secClk(a), 0);
  const sumCold = old.reduce((s, a) => s + _secClk(a), 0);
  L.push(`Показы: *${sumI}* ${_secDelta(sumI, sumIold)}`);
  L.push(`Клики: *${sumC}* ${_secDelta(sumC, sumCold)}`);
  L.push("");

  const moved = [];
  for (const a of cur) {
    const o = oldBySlug[a.slug];
    if (!o) continue;
    const pNew = _secPos(a), pOld = _secPos(o);
    if (pNew === null || pOld === null) continue;
    const diff = pOld - pNew; // >0 = поднялась в выдаче
    if (Math.abs(diff) >= 0.5) moved.push({ title: a.title, diff, pNew });
  }
  moved.sort((a, b) => b.diff - a.diff);

  const up = moved.filter(m => m.diff > 0).slice(0, 5);
  const down = moved.filter(m => m.diff < 0).slice(0, 5);
  if (up.length) {
    L.push("*⬆️ Растут в выдаче:*");
    for (const m of up) L.push(`• ${escapeMd(String(m.title).slice(0, 40))} — поз. ${m.pNew.toFixed(1)} (↑${m.diff.toFixed(1)})`);
    L.push("");
  }
  if (down.length) {
    L.push("*⬇️ Падают в выдаче:*");
    for (const m of down) L.push(`• ${escapeMd(String(m.title).slice(0, 40))} — поз. ${m.pNew.toFixed(1)} (↓${Math.abs(m.diff).toFixed(1)})`);
    L.push("");
  }
  const newOnes = cur.filter(a => !oldBySlug[a.slug] && _secImpr(a) > 0);
  if (newOnes.length) {
    L.push("*🆕 Начали показываться:*");
    for (const a of newOnes.slice(0, 5)) L.push(`• ${escapeMd(String(a.title).slice(0, 40))} — ${_secImpr(a)} 👁`);
  }
  return L.join("\n");
}

// ── 🔎 ЗАПРОСЫ: топ + на грани топ-10 ──
function renderSectionQueries(report) {
  const articles = report.articles || [];
  const site = report.site || {};
  const L = ["🔎 *Поисковые запросы*", ""];

  // Приоритет: запросы по всему сайту (site.top_queries). Фолбэк — из статей.
  let allQ = [];
  if (Array.isArray(site.top_queries) && site.top_queries.length) {
    allQ = site.top_queries.map(q => ({
      q: q.query, impr: q.impressions || 0, clk: q.clicks || 0, pos: q.position,
    }));
  } else {
    for (const a of articles) {
      for (const q of (a.top_queries || [])) {
        if (q.query) allQ.push({ q: q.query, impr: q.impressions || 0, clk: q.clicks || 0, pos: q.position });
      }
    }
  }
  allQ.sort((a, b) => b.impr - a.impr);

  if (allQ.length) {
    L.push("*Топ запросов (показы · клики):*");
    for (const q of allQ.slice(0, 12)) {
      const pos = (typeof q.pos === "number") ? ` · поз. ${q.pos.toFixed(1)}` : "";
      const clk = q.clk ? ` · ${q.clk} 🖱` : "";
      L.push(`• ${escapeMd(q.q.slice(0, 34))} — ${q.impr} 👁${clk}${pos}`);
    }
    L.push("");
  }

  const edge = allQ.filter(q => typeof q.pos === "number" && q.pos > 10 && q.pos <= 20);
  edge.sort((a, b) => a.pos - b.pos);
  if (edge.length) {
    L.push("*🎯 На грани топ-10 (дожать):*");
    for (const q of edge.slice(0, 8)) {
      L.push(`• ${escapeMd(q.q.slice(0, 32))} — поз. ${q.pos.toFixed(1)} (${q.impr} 👁)`);
    }
    L.push("");
    L.push("_Оптимизируй страницы под эти запросы — они близко к первой странице._");
  }
  if (!allQ.length) {
    L.push("_Запросов пока нет — Search Console наберёт данные за несколько дней._");
  }
  return L.join("\n");
}

// ── 🌍 ТРАФИК: источники + страны + устройства ──
function renderSectionTraffic(report) {
  const ga4 = report.ga4 || {};
  const L = ["🌍 *Источники трафика*", ""];
  if (!ga4.available) {
    L.push("_GA4 ещё не отдаёт данные. Появится: каналы, страны, устройства._");
    return L.join("\n");
  }
  const src = ga4.traffic_sources || {};
  const entries = Object.entries(src).sort((a, b) => b[1] - a[1]);
  if (entries.length) {
    L.push("*Каналы (пользователи):*");
    for (const [k, v] of entries.slice(0, 8)) {
      L.push(`• ${escapeMd(String(k))}: *${v}*`);
    }
    L.push("");
  }
  const geo = ga4.by_country || {};
  const geoE = Object.entries(geo).sort((a, b) => b[1] - a[1]).slice(0, 6);
  if (geoE.length) {
    L.push("*Страны:*");
    for (const [k, v] of geoE) L.push(`• ${escapeMd(String(k))}: *${v}*`);
    L.push("");
  }
  const dev = ga4.by_device || {};
  const devE = Object.entries(dev).sort((a, b) => b[1] - a[1]);
  if (devE.length) {
    L.push("*Устройства:*");
    for (const [k, v] of devE) L.push(`• ${escapeMd(String(k))}: *${v}*`);
  }
  if (!entries.length && !geoE.length && !devE.length) {
    L.push("_Данных пока нет._");
  }
  return L.join("\n");
}

// ── ⚙️ GA4-ПОВЕДЕНИЕ: вовлечённость, отказы, время ──
function renderSectionGA4(report) {
  const ga4 = report.ga4 || {};
  const articles = report.articles || [];
  const L = ["⚙️ *GA4 — поведение на страницах*", ""];
  if (!ga4.available) {
    L.push("_GA4 ещё не отдаёт данные (нужны визиты + 24–48ч)._");
    L.push("");
    L.push("Появится по каждой странице:");
    L.push("• просмотры и пользователи");
    L.push("• вовлечённость (engagement rate)");
    L.push("• показатель отказов (bounce rate)");
    L.push("• среднее время вовлечения");
    return L.join("\n");
  }
  const t = ga4.totals || {};
  L.push(`👥 Пользователи: *${t.users || 0}* · 🖥 Сессии: *${t.sessions || 0}* · 👁 Просмотры: *${t.views || 0}*`);
  L.push("");

  const beh = ga4.behavior_by_page || {};
  const rows = Object.entries(beh)
    .map(([path, b]) => ({ path, ...b }))
    .sort((a, b) => (b.views || 0) - (a.views || 0))
    .slice(0, 8);
  if (rows.length) {
    L.push("*По страницам (просмотры · вовлеч. · отказы):*");
    for (const r of rows) {
      const eng = (r.engagement_rate != null) ? `${(r.engagement_rate * 100).toFixed(0)}%` : "—";
      const bounce = (r.bounce_rate != null) ? `${(r.bounce_rate * 100).toFixed(0)}%` : "—";
      L.push(`• ${escapeMd(String(r.path).slice(0, 36))}`);
      L.push(`   ${r.views || 0} 👁 · вовлеч. ${eng} · отказы ${bounce}`);
    }
    L.push("");
  }

  const problem = [];
  for (const a of articles) {
    const views = (a.behavior && a.behavior.views) || 0;
    const cr = a.conv_rate || 0;
    if (views >= 20 && cr < 0.01) problem.push({ title: a.title, views, cr });
  }
  problem.sort((a, b) => b.views - a.views);
  if (problem.length) {
    L.push("*⚠️ Читают, но не кликают:*");
    for (const p of problem.slice(0, 5)) {
      L.push(`• ${escapeMd(String(p.title).slice(0, 40))} — ${p.views} 👁, ${(p.cr * 100).toFixed(1)}%`);
    }
  }
  return L.join("\n");
}

// helpers для разделов дашборда
const SECTION_SRC_LABELS = {
  side_widget: "Боковой виджет",
  mobile_bar: "Моб. панель",
  final_cta: "Финальный CTA",
  partner_card: "Карточка партнёра",
  link: "Ссылка в тексте",
};
function _secDelta(cur, old) {
  const d = cur - old;
  if (d === 0) return "→ 0";
  const arrow = d > 0 ? "⬆️" : "⬇️";
  const pct = old ? ` (${d > 0 ? "+" : ""}${(d / old * 100).toFixed(0)}%)` : "";
  return `${arrow} ${d > 0 ? "+" : ""}${d}${pct}`;
}

// Рендер компактной сводки из structured report.json.
// Тихо деградирует: нет GA4 → показывает только SEO; отчёт пуст → приглашает
// собрать. Все длинные заголовки статей обрезаются, спецсимволы экранируются.
function renderInstantSummary(report) {
  const L = ["📊 *Аналитика KOZYR* — последняя сводка"];

  // Пустой/несобранный отчёт (нет by_category или совсем нет данных).
  const cats = (report && report.by_category) || null;
  const articles = (report && report.articles) || [];
  const hasData = cats && (articles.length > 0 ||
    ["winners", "needs_boost", "flat", "new"].some(k => (cats[k] || []).length > 0));
  if (!report || !hasData) {
    L.push("");
    L.push("_Собранного отчёта пока нет (или он пуст). Выбери период ниже — " +
      "бот соберёт свежие данные из Search Console + GA4 и пришлёт полную сводку._");
    return L.join("\n");
  }

  const ga4 = report.ga4 || {};
  if (report.period_label) L.push(`_${report.period_label}_`);
  if (report.collected_at) {
    L.push(`_обновлено: ${String(report.collected_at).slice(0, 16).replace("T", " ")} UTC_`);
  }
  L.push("");

  // ── Итоги за период (только если GA4 подключён) ──
  if (ga4.available) {
    const t = ga4.totals || {};
    const totalClicks = articles.reduce(
      (s, a) => s + ((a.conversions && a.conversions.total) || 0), 0);
    const views = t.views || 0;
    const cr = views ? (totalClicks / views * 100) : 0;
    L.push("*⚡ Итоги за период*");
    L.push(`👥 Пользователи: *${t.users || 0}*  ·  👁 Просмотры: *${views}*`);
    L.push(`🎯 Переходы к партнёрам: *${totalClicks}*  ·  📈 Конв.: *${cr.toFixed(2)}%*`);
    L.push("");

    // Лидер по переходам к партнёрам
    const byConv = articles
      .filter(a => a.conversions && a.conversions.total > 0)
      .sort((a, b) => b.conversions.total - a.conversions.total);
    if (byConv.length) {
      L.push("*🔥 Топ по переходам*");
      for (const a of byConv.slice(0, 3)) {
        L.push(`• ${escapeMd(String(a.title || "").slice(0, 42))} — *${a.conversions.total}*`);
      }
      L.push("");
    }
  } else {
    L.push("_GA4 не подключён — показаны только SEO-данные. " +
      "Задай `GA4\\_PROPERTY\\_ID` для конверсий и поведения._");
    L.push("");
  }

  // ── Поиск по ВСЕМУ САЙТУ (Search Console, все страницы) ──
  const site = report.site || {};
  const st = site.totals || null;
  if (st && (st.impressions || st.clicks)) {
    const sctr = (st.ctr || 0) * 100;
    const spos = (typeof st.position === "number") ? st.position.toFixed(1) : "—";
    L.push("*🔍 Поиск по всему сайту*");
    L.push(`👁 Показы: *${st.impressions || 0}*  ·  🖱 Клики: *${st.clicks || 0}*  ·  CTR *${sctr.toFixed(1)}%*`);
    L.push(`📍 Средняя позиция: *${spos}*  ·  📄 Страниц с трафиком: *${site.pages_count || 0}*`);
    L.push("");
  }

  // ── SEO-статус ──
  const w = (cats.winners || []).length;
  const b = (cats.needs_boost || []).length;
  const f = (cats.flat || []).length;
  const n = (cats.new || []).length;
  L.push("*🔍 SEO-статус статей блога*");
  L.push(`🟢 Winners: *${w}*  ·  🟡 Boost: *${b}*  ·  🔴 Flat: *${f}*  ·  ⚫ New: *${n}*`);

  // ── Что дожать в топ ──
  const boost = cats.needs_boost || [];
  if (boost.length) {
    L.push("");
    L.push("*🟡 Дожать в топ (высокий потенциал):*");
    for (const it of boost.slice(0, 3)) {
      const s = it.stats || {};
      L.push(`• ${escapeMd(String(it.title || "").slice(0, 42))} — ` +
        `${s.impressions || 0} показов · поз. ${s.position || "—"}`);
    }
  }

  L.push("");
  L.push("_Свежий сбор за нужный период — кнопками ниже._");
  return L.join("\n");
}

// Присылает полный markdown-отчёт (analytics/report.md) из репозитория,
// разбивая на части под лимит Telegram. Тело — plain text, чтобы разметка
// внутри отчёта не ломала Markdown-парсер бота.
async function sendFullReport(chatId, env) {
  const f = await ghReadFile("analytics/report.md", env);
  if (!f || !String(f.text).trim()) {
    await sendMessage(chatId, env,
      "⚠️ `analytics/report.md` пуст. Сначала собери отчёт — кнопка периода выше.");
    return;
  }
  const body = f.text;
  const CHUNK = 3800;
  const total = Math.ceil(body.length / CHUNK);
  for (let i = 0; i < total; i++) {
    const part = body.slice(i * CHUNK, (i + 1) * CHUNK);
    const header = total > 1 ? `📄 Полный отчёт (${i + 1}/${total})\n\n` : "";
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text: (header + part).slice(0, 4000),
        disable_web_page_preview: true,
      }),
    });
  }
}

// Запуск сбора аналитики за период. periodArg:
//   "default"/"" → 60 дней · "week"/"month"/"quarter"/"year" → пресет ·
//   число (строка) → последние N дней.
async function runAnalytics(chatId, periodArg, env) {
  const inputs = {};
  let note = "за последние 60 дней";
  const presets = {
    week: "за неделю", month: "за месяц",
    quarter: "за квартал", year: "за год",
  };
  const p = (periodArg || "").trim().toLowerCase();
  if (presets[p]) {
    inputs.period = p;
    note = presets[p];
  } else if (/^\d{1,4}$/.test(p)) {
    inputs.days = p;
    note = `за ${p} дней`;
  }
  // "default"/пусто → inputs пустые → воркфлоу возьмёт дефолт 60 дней.
  const ok = await triggerWorkflow("analytics-report.yml", inputs, env);
  await sendMessage(chatId, env, ok
    ? `📊 Запустил сбор аналитики (${note}). Через ~2 минуты придёт сводка.`
    : "❌ Не удалось запустить analytics-report.yml.");
}

async function cmdStatus(chatId, args, msg, env) {
  // Что показать:
  // - активные workflow runs
  // - количество _pending статей
  // - открытые edit-сессии
  const runs = await ghListRuns(env);
  const pending = await ghListDir("_pending", env);
  const editSessions = await ghListDir(".bot_state/edit_sessions", env);

  const activeRuns = (runs || []).filter(r => r.status !== "completed").slice(0, 5);
  const lines = ["🩺 *Статус KOZYR bot*", ""];
  lines.push(`⚙️ Активные workflows: *${activeRuns.length}*`);
  for (const r of activeRuns) {
    lines.push(`  • ${escapeMd(r.name)} · ${escapeMd(r.status)} · [лог](${r.html_url})`);
  }
  lines.push("");
  lines.push(`📝 Статей в \`_pending/\`: *${pending?.length || 0}*`);
  for (const p of (pending || []).slice(0, 8)) {
    lines.push(`  • ${escapeMd(p.name)}`);
  }
  lines.push("");
  lines.push(`✏️ Открытых сессий редактирования: *${editSessions?.length || 0}*`);

  await sendMessage(chatId, env, lines.join("\n"));
}

async function cmdHistory(chatId, args, msg, env) {
  const slug = args[0];
  if (!slug) {
    await sendMessage(chatId, env, "Использование: `/history slug`");
    return;
  }
  const history = await ghReadJSON(`.bot_state/history/${slug}.json`, env);
  if (!history || !Array.isArray(history) || history.length === 0) {
    await sendMessage(chatId, env, `📖 Истории по \`${escapeMd(slug)}\` нет.`);
    return;
  }
  const lines = [`📖 *История: ${escapeMd(slug)}*`, ""];
  for (const e of history.slice(-15)) {
    const details = e.details && Object.keys(e.details).length
      ? " · " + escapeMd(JSON.stringify(e.details).slice(0, 100))
      : "";
    lines.push(`• ${escapeMd(e.at || "")} — *${escapeMd(e.action || "?")}*${details}`);
  }
  await sendMessage(chatId, env, lines.join("\n"));
}

async function cmdEdit(chatId, args, msg, env) {
  const slug = args[0];
  const field = args[1];
  if (!slug) {
    await sendMessage(chatId, env,
      "Использование: `/edit slug` — показать меню полей\n" +
      "или `/edit slug field` — сразу начать редактировать поле");
    return;
  }
  if (!field) {
    await showEditFieldMenu(slug, msg, env);
    return;
  }
  await startEditSession(slug, field, msg, env);
}

async function cmdAb(chatId, args, msg, env) {
  const slug = args[0];
  if (!slug) {
    await sendMessage(chatId, env,
      "Использование: `/ab slug` — предложить A/B по одному из полей.");
    return;
  }
  // Просто рендерим кнопки — Worker их обработает
  const kb = [[
    { text: "A/B по meta_title", callback_data: `edit_menu:${slug}` },
    { text: "A/B по description", callback_data: `edit_menu:${slug}` },
  ]];
  await sendMessage(chatId, env,
    `🧪 A/B тестирование пока в бете. Пока делаем через правку meta.json и включение флага \`ab_test: true\`. Полноценная реализация — в следующей итерации.`);
}

async function cmdCancel(chatId, args, msg, env) {
  const session = await getOpenEditSessionForChat(chatId, env);
  if (!session) {
    await sendMessage(chatId, env, "🤷 Нет открытой сессии редактирования.");
    return;
  }
  await ghDeleteFile(`.bot_state/edit_sessions/${session.slug}.json`, "close edit session", env);
  await sendMessage(chatId, env,
    `❌ Сессия правки \`${escapeMd(session.slug)}\` / \`${escapeMd(session.field)}\` закрыта. Ничего не изменено.`);
}

async function cmdPending(chatId, args, msg, env) {
  // v2 multilang: показываем _pending и _pending_* — все языки
  const knownPendingDirs = ["_pending", "_pending_uk", "_pending_pl", "_pending_kk"];
  const grouped = {};
  const slugToLangs = {}; // slug → [langs]
  let total = 0;
  for (const dir of knownPendingDirs) {
    const items = await ghListDir(dir, env);
    if (items && items.length > 0) {
      grouped[dir] = items;
      total += items.length;
      const lang = dir === "_pending" ? "ru" : dir.replace("_pending_", "");
      for (const item of items) {
        if (!slugToLangs[item.name]) slugToLangs[item.name] = [];
        slugToLangs[item.name].push(lang);
      }
    }
  }
  // Партнёрские страницы на ревью (_pending_partner/) — покажем их ниже статей.
  const pendingPartners = await ghListDir("_pending_partner", env);
  const hasPartners = Array.isArray(pendingPartners) && pendingPartners.length > 0;

  if (total === 0 && !hasPartners) {
    await sendMessage(chatId, env, "📭 Ни в `_pending*/`, ни в `_pending_partner/` ничего нет.");
    return;
  }

  // Заголовок (только если есть статьи)
  if (total > 0) {
    await sendMessage(chatId, env,
      `📝 *Pending статей: ${total}* — по каждой есть кнопки быстрого действия ниже:`);
  }

  // Для каждой статьи — отдельное сообщение с инлайн-кнопками
  const slugs = Object.keys(slugToLangs).slice(0, 10); // не спамим
  for (const slug of slugs) {
    const langs = slugToLangs[slug];
    const langLine = langs.map(l => {
      const flag = {ru: "🇷🇺", uk: "🇺🇦", pl: "🇵🇱", kk: "🇰🇿"}[l] || "";
      return `${flag} ${l.toUpperCase()}`;
    }).join(" + ");

    const kb = [];
    // Главная — публикация
    if (langs.length > 1) {
      kb.push([
        { text: "✅ Опубликовать все языки",
          callback_data: `publish_all:${slug}` },
      ]);
      // Точечная
      const row = langs.map(l => {
        const flag = {ru: "🇷🇺", uk: "🇺🇦", pl: "🇵🇱", kk: "🇰🇿"}[l] || "";
        return { text: `🚀 Только ${flag}`, callback_data: `publish_lang:${l}:${slug}` };
      });
      kb.push(row);
    } else {
      // Одноязычная — стандартный publish
      kb.push([{ text: "✅ Опубликовать", callback_data: `publish:${slug}` }]);
    }
    // Правка и исходники
    const editRow = langs.map(l => {
      const flag = {ru: "🇷🇺", uk: "🇺🇦", pl: "🇵🇱", kk: "🇰🇿"}[l] || "";
      return { text: `✏️ ${flag}`, callback_data: `edit_menu_lang:${l}:${slug}` };
    });
    kb.push(editRow);
    kb.push([
      { text: "🧾 Исходники", callback_data: `sources:${slug}` },
      { text: langs.length > 1 ? "❌ Отклонить всё" : "❌ Отклонить",
        callback_data: langs.length > 1 ? `reject_all:${slug}` : `reject:${slug}` },
    ]);

    await sendMessage(chatId, env,
      `📄 \`${escapeMd(slug)}\`\n🌍 ${langLine}`, kb);
  }
  if (Object.keys(slugToLangs).length > 10) {
    await sendMessage(chatId, env,
      `… и ещё ${Object.keys(slugToLangs).length - 10} статей. Показал первые 10.`);
  }

  // ── Партнёрские страницы на ревью (_pending_partner/) ──
  // Показываем ВСЕГДА, даже если оператор потерял сообщение с кнопкой
  // «Опубликовать» из партнёрского флоу — отсюда партнёра можно довести до прода.
  if (hasPartners) {
    await sendMessage(chatId, env,
      `🎯 *Партнёры на ревью: ${pendingPartners.length}* (\`_pending_partner/\`):`);
    for (const item of pendingPartners.slice(0, 10)) {
      const pid = item.name;
      if (!validPartnerId(pid)) continue;
      const kb = [
        [{ text: "🌐 Опубликовать в прод", callback_data: `ppublish:${pid}` }],
        [{ text: "🔁 Пересобрать превью", callback_data: `pconfirm:${pid}` }],
      ];
      await sendMessage(chatId, env, `🎯 \`${escapeMd(pid)}\``, kb);
    }
  }
}

// v2 multilang: /countries — показать все настроенные страны и их языки
async function cmdCountries(chatId, args, msg, env) {
  // Читаем country_config.py напрямую из репозитория (простой парсинг)
  const f = await ghReadFile("automation/country_config.py", env);
  if (!f) {
    await sendMessage(chatId, env, "⚠️ `automation/country_config.py` не найден.");
    return;
  }
  // Наивный парсер: ищем блоки '"ua": { "name": "...", "flag": "...", "languages": [...] }'
  const countries = [];
  const src = f.text;
  const re = /"([a-z]{2})":\s*\{\s*"name":\s*"([^"]+)",\s*"flag":\s*"([^"]+)",\s*"languages":\s*\[([^\]]+)\]/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    const code = m[1];
    const name = m[2];
    const flag = m[3];
    const langs = m[4].split(",").map(x => x.trim().replace(/"/g, "")).filter(Boolean);
    countries.push({ code, name, flag, langs });
  }

  if (countries.length === 0) {
    await sendMessage(chatId, env,
      "⚠️ Не удалось распарсить `country_config.py`. Открой его вручную.");
    return;
  }

  const lines = [`🌍 *Настроенные страны* (${countries.length})`, ""];
  for (const c of countries) {
    lines.push(`${c.flag} *${escapeMd(c.name)}* — код \`${c.code}\``);
    lines.push(`  Языки: ${c.langs.map(l => "`" + l + "`").join(" · ")}`);
    lines.push("");
  }
  lines.push("Добавить страну: правь `automation/country_config.py`.");
  lines.push("Пример: см. закомментированные заготовки внутри файла.");
  await sendMessage(chatId, env, lines.join("\n"));
}

// v2 multilang: /translate slug lang — вручную перевести уже готовую статью
async function cmdTranslate(chatId, args, msg, env) {
  const slug = args[0];
  const targetLang = args[1];
  if (!slug || !targetLang) {
    await sendMessage(chatId, env,
      "Использование: `/translate slug uk` — перевести статью на язык uk.\n" +
      "Статья должна быть в `_pending/{slug}/` (не опубликованная).\n" +
      "Или в `ua/blog/{slug}/` (опубликованная) — тогда бот прочтёт body.md.");
    return;
  }
  if (!/^[a-z]{2}$/.test(targetLang)) {
    await sendMessage(chatId, env,
      "⚠️ Целевой язык должен быть 2-буквенным кодом (ru/uk/pl…)");
    return;
  }
  const ok = await triggerWorkflow("translate-article.yml", {
    slug, target_lang: targetLang,
  }, env);
  await sendMessage(chatId, env, ok
    ? `🌐 Запустил перевод \`${escapeMd(slug)}\` → ${targetLang.toUpperCase()}. Через 1-2 минуты будет готово.`
    : "❌ Не удалось запустить translate-article.yml.");
}

// ═══════════════════════════════════════════════════════════════════════════
//  ДОБАВЛЕНИЕ ПАРТНЁРА (свободный текст → Claude парсит → страница)
// ═══════════════════════════════════════════════════════════════════════════
//
// Два независимых процесса общаются ТОЛЬКО через репозиторий и Telegram:
//   • Worker (этот файл)  — держит сессию, пишет ввод, триггерит workflow,
//                           обрабатывает кнопки.
//   • parse-partner.yml   — читает ввод, зовёт Claude, коммитит черновик
//     (parse_partner.py)    _partner_drafts/{id}.json и сам шлёт сводку
//                           с кнопками pconfirm/pmore/pcountry/pcancel.
//
// Поэтому Worker НИКОГДА не полагается на то, что помнит id черновика:
//   — в кнопках id приходит из callback_data (его кладёт parse_partner.py);
//   — если авто-сводка не дошла, есть кнопка «Проверить черновик»
//     (pshow_latest), которая находит свежайший черновик в _partner_drafts/.
//
// Машина состояний сессии (.bot_state/partner_sessions/{chatId}.json):
//   await_text ──(текст)──► parsing ──(сводка от питона / pshow)──► await_confirm
//        ▲                     │                                        │
//        │                     └──(pmore / любой текст)──► await_more ──┘
//        │                                                              │
//   await_more ──(текст)──► parsing                          (pconfirm)─┤
//                                                                       ▼
//                                                                  generated
//                                                              (ppublish/pcancel/TTL → удаление)
//
// Ключевой инвариант: свободный текст в ЛЮБОЙ активной стадии трактуется как
// ввод/дополнение анкеты и не теряется.

const PARTNER_SESSION_TTL_MS = 60 * 60 * 1000;              // 60 минут
const PARTNER_INPUT_PATH = (chatId) => `.bot_state/partner_input/${chatId}.txt`;
const PARTNER_DRAFT_PATH = (id) => `_partner_drafts/${id}.json`;
// Страны, которые реально настроены в automation/country_config.py.
// Если добавляешь страну туда — добавь код сюда, иначе страница уйдёт в путь
// /xx/, которого нет в конфиге, и каталог её не подхватит.
const PARTNER_COUNTRIES = ["ua", "kz", "pl", "by"];

// Поля черновика, которые оператор может поправить кнопками (без переписывания
// всего описания). type задаёт, как трактовать введённый текст.
const PARTNER_EDIT_FIELDS = {
  name:        { label: "Название",   type: "str",  hint: "новое название (напр. Royal Club)" },
  rakeLabel:   { label: "Рейкбек",    type: "rake", hint: "как показывать рейкбек (напр. «до 35%» или «нет»)" },
  bonus:       { label: "Бонус",      type: "list", hint: "бонусы через запятую или с новой строки" },
  limits:      { label: "Лимиты",     type: "list", hint: "лимиты через запятую (NL10, NL25, NL50)" },
  payoutLabel: { label: "Выплаты",    type: "str",  hint: "скорость выплат (напр. «0–24 часа», «мгновенно»)" },
  score:       { label: "Score",      type: "num",  hint: "оценку 1–10 (напр. 8.4)" },
  ref_url:     { label: "Реф-ссылка", type: "url",  hint: "ссылку кнопки «Перейти» (https://… или t.me/…)" },
  currency:    { label: "Валюта",     type: "cur",  hint: "валюту: UAH / USD / EUR / USDT" },
  note:        { label: "Описание",   type: "str",  hint: "краткое описание в 1 предложение для карточки" },
};

function partnerEditMenuKb(draftId) {
  const keys = Object.keys(PARTNER_EDIT_FIELDS);
  const kb = [];
  for (let i = 0; i < keys.length; i += 2) {
    kb.push(keys.slice(i, i + 2).map(k => ({
      text: "✏️ " + PARTNER_EDIT_FIELDS[k].label,
      callback_data: `pfield:${draftId}:${k}`,
    })));
  }
  kb.push([{ text: "← Назад к сводке", callback_data: `pshow:${draftId}` }]);
  return kb;
}

// Применяет введённое значение к одному полю черновика (по типу поля),
// сохраняет анкету и заново показывает сводку.
async function applyPartnerFieldValue(chatId, session, rawValue, env) {
  const draftId = session.draft_id;
  const field = session.edit_field;
  const meta = PARTNER_EDIT_FIELDS[field] ||
    (field === "acceptedCountries" ? { label: "Принимает из", type: "countries" } : null);
  const draft = await ghReadJSON(PARTNER_DRAFT_PATH(draftId), env);
  if (!draft || !meta) {
    session.stage = "await_confirm"; session.edit_field = null;
    await savePartnerSession(chatId, session, env);
    await sendMessage(chatId, env, "⚠️ Черновик не найден — правка отменена.");
    return;
  }

  const value = (rawValue || "").trim();
  let applied = value;

  switch (meta.type) {
    case "list":
      draft[field] = value.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
      applied = draft[field].join(", ") || "—";
      break;
    case "countries": {
      const codes = value.toLowerCase().split(/[\s,;]+/).map(s => s.trim())
        .filter(s => s === "all" || /^[a-z]{2}$/.test(s));
      if (!codes.length) { await sendMessage(chatId, env, "⚠️ Не вижу кодов стран. Пример: `ua, pl, de`. Попробуй ещё раз."); return; }
      draft.acceptedCountries = codes;
      applied = codes.join(", ");
      break;
    }
    case "num": {
      let n = parseFloat(value.replace(",", "."));
      if (isNaN(n)) { await sendMessage(chatId, env, "⚠️ Нужно число, напр. `8.4`. Попробуй ещё раз."); return; }
      n = Math.max(1, Math.min(10, n));
      draft.score = Math.round(n * 10) / 10;
      applied = String(draft.score);
      break;
    }
    case "url":
      if (value && !/^(https?:\/\/|t\.me\/|tg:\/\/)/i.test(value)) {
        await sendMessage(chatId, env, "⚠️ Ссылка должна начинаться с `https://`, `t.me/` или `tg://`. Попробуй ещё раз."); return;
      }
      draft[field] = value;
      break;
    case "cur":
      draft[field] = value.toUpperCase();
      applied = draft[field];
      break;
    case "rake": {
      draft.rakeLabel = value;
      const m = value.match(/(\d+(?:[.,]\d+)?)\s*%/);
      if (m) draft.rake = parseFloat(m[1].replace(",", "."));
      else if (/^(нет|none|0|без)/i.test(value)) draft.rake = null;
      applied = value;
      break;
    }
    default: // str
      draft[field] = value;
  }

  // Поле заполнено — убираем его из «не указано».
  if (Array.isArray(draft._missing)) {
    const lab = meta.label.toLowerCase();
    draft._missing = draft._missing.filter(m => !String(m).toLowerCase().includes(lab));
  }

  await ghWriteFile(PARTNER_DRAFT_PATH(draftId), JSON.stringify(draft, null, 2),
    `partner: edit ${field} for ${draftId}`, env);

  session.stage = "await_confirm";
  session.edit_field = null;
  await savePartnerSession(chatId, session, env);

  await sendMessage(chatId, env, `✅ *${meta.label}* обновлено: ${escapeMd(String(applied))}`);
  await showPartnerDraftSummary(chatId, draftId, env);
}
const COUNTRY_FLAGS = { ua: "🇺🇦", kz: "🇰🇿", pl: "🇵🇱", by: "🇧🇾", ru: "🇷🇺" };

// ── /addpartner — старт: открываем сессию, ждём описание ──
async function cmdAddPartner(chatId, args, msg, env) {
  // Свежая сессия. Чистим прошлый ввод, чтобы остатки старого описания
  // не подмешались в новый парсинг.
  await ghDeleteFile(PARTNER_INPUT_PATH(chatId), "partner: reset input", env).catch(() => {});
  const session = {
    chat_id: chatId,
    stage: "await_text",
    started_at: new Date().toISOString(),
    draft_id: null,
  };
  await savePartnerSession(chatId, session, env);

  const text = `➕ *Добавление партнёра*\n\n` +
    `Опиши партнёра *одним сообщением* — своими словами, в свободной форме. ` +
    `Чем больше деталей, тем полнее страница. Можно прямо скопировать заполненную анкету.\n\n` +
    `*Желательно указать:*\n` +
    `• Название и тип (покер-рум или клуб)\n` +
    `• Приложение (ClubGG / PPPoker / X-Poker / PokerBros / Upoker) — если это клуб\n` +
    `• Страна аудитории, валюта расчётов\n` +
    `• Рейкбек (% или «нет — только бонусы»)\n` +
    `• Бонусы, форматы (кэш/турниры/спины), лимиты\n` +
    `• Платежи (карта / крипта — какие сети), сроки выплат\n` +
    `• Плюсы и минусы, как начать играть\n` +
    `• Ссылка для кнопки «Перейти»\n\n` +
    `Я сам разберу текст на параметры и покажу, что понял.\n\n` +
    `Отмена: /cancel`;
  await sendMessage(chatId, env, text);
}

// ── Приём свободного текста в рамках партнёрской сессии ──
async function handlePartnerAnswer(chatId, text, message, env) {
  const session = await getPartnerSession(chatId, env);
  if (!session) return;   // сессия истекла между проверкой и обработкой

  const stage = session.stage || "await_text";

  // Правка одного поля кнопкой: пришло новое значение — применяем к черновику.
  if (stage === "edit_field" && session.edit_field && validPartnerId(session.draft_id)) {
    await applyPartnerFieldValue(chatId, session, text, env);
    return;
  }

  // Первый ввод описания.
  if (stage === "await_text") {
    await triggerPartnerParse(chatId, text, env, { append: false });
    return;
  }

  // Дополнение/исправление. Сюда попадаем как по кнопке «Дополнить» (await_more),
  // так и если оператор просто продолжил писать, не нажав кнопку
  // (parsing / await_confirm / generated) — текст не теряем.
  if (stage === "await_more" || stage === "parsing" ||
      stage === "await_confirm" || stage === "generated") {
    await triggerPartnerParse(chatId, text, env, { append: true });
    return;
  }

  // Неизвестная стадия — на всякий случай трактуем как новый ввод.
  await triggerPartnerParse(chatId, text, env, { append: false });
}

// ── Общая процедура: записать ввод (+ опц. дописать) и запустить парсинг ──
async function triggerPartnerParse(chatId, text, env, { append }) {
  const path = PARTNER_INPUT_PATH(chatId);
  let payloadText = text;
  let appendedNote = "";

  if (append) {
    const existing = await ghReadFile(path, env);
    if (existing && existing.text && existing.text.trim()) {
      payloadText = existing.text.trim() + "\n\n--- дополнение ---\n" + text;
      appendedNote = " (учту вместе с прежним описанием)";
    } else {
      // Прежнего текста нет (истёк/подчищен) — дополнение станет основным.
      appendedNote = " (прежнее описание не найдено — беру этот текст как основной)";
    }
  }

  const wrote = await ghWriteFile(path, payloadText,
    `partner: ${append ? "дополнение" : "описание"} от ${chatId}`, env);
  if (!wrote) {
    await sendMessage(chatId, env, "❌ Не удалось сохранить описание. Попробуй ещё раз.");
    return;
  }

  const ok = await triggerWorkflow("parse-partner.yml",
    { chat_id: String(chatId), input_path: path }, env);
  if (!ok) {
    await sendMessage(chatId, env,
      "❌ Не удалось запустить парсинг (parse-partner.yml). Проверь GITHUB_TOKEN и наличие workflow.");
    return;
  }

  // Стадия → ждём результат. draft_id пока неизвестен — его назначит питон.
  const session = (await getPartnerSession(chatId, env)) ||
    { chat_id: chatId, started_at: new Date().toISOString() };
  session.stage = "parsing";
  await savePartnerSession(chatId, session, env);

  await sendMessage(chatId, env,
    `🧠 Разбираю описание${appendedNote}… обычно ~20–40 секунд.\n\n` +
    `Пришлю, что понял, и кнопки. Если сводка не придёт за минуту — жми:`,
    [[{ text: "🔄 Проверить черновик", callback_data: "pshow_latest" }]]);
}

// ── Единый диспетчер всех партнёрских callback'ов ──
async function handlePartnerCallback(action, segments, cb, env) {
  const chatId = cb.message.chat.id;

  if (action === "pshow_latest") {
    await answerCallback(cb.id, env, "🔎 Ищу черновик…");
    await showLatestPartnerDraft(chatId, env);
    return;
  }

  if (action === "pshow") {
    const draftId = segments[1];
    if (!validPartnerId(draftId)) { await answerCallback(cb.id, env, "⚠️ Некорректный id"); return; }
    await answerCallback(cb.id, env, "📋 Показываю черновик");
    await showPartnerDraftSummary(chatId, draftId, env);
    return;
  }

  // Меню правки полей.
  if (action === "pedit") {
    const draftId = segments[1];
    if (!validPartnerId(draftId)) { await answerCallback(cb.id, env, "⚠️ Некорректный id"); return; }
    await answerCallback(cb.id, env, "✏️ Что исправить?");
    await sendMessage(chatId, env,
      "✏️ *Что исправить?* Выбери поле — пришлю подсказку, а ты одним сообщением введёшь новое значение.",
      partnerEditMenuKb(draftId));
    return;
  }

  // Выбрано поле → просим значение и переводим сессию в edit_field.
  if (action === "pfield") {
    const draftId = segments[1];
    const field = segments[2];
    if (!validPartnerId(draftId) || !PARTNER_EDIT_FIELDS[field]) {
      await answerCallback(cb.id, env, "⚠️ Некорректное поле"); return;
    }
    const f = PARTNER_EDIT_FIELDS[field];
    await answerCallback(cb.id, env, `✏️ ${f.label}`);
    const session = (await getPartnerSession(chatId, env)) ||
      { chat_id: chatId, started_at: new Date().toISOString() };
    session.stage = "edit_field";
    session.draft_id = draftId;
    session.edit_field = field;
    await savePartnerSession(chatId, session, env);
    await sendMessage(chatId, env,
      `✏️ *${f.label}* — пришли ${f.hint} одним сообщением.`,
      [[{ text: "← Отмена правки", callback_data: `pshow:${draftId}` }]]);
    return;
  }

  // Откуда партнёр принимает игроков (acceptedCountries) — кнопками.
  if (action === "pacc") {
    const draftId = segments[1];
    const mode = segments[2];
    if (!validPartnerId(draftId)) { await answerCallback(cb.id, env, "⚠️ Некорректный id"); return; }

    if (!mode) {
      await answerCallback(cb.id, env, "🌐 Откуда принимает?");
      await sendMessage(chatId, env,
        "🌐 *Откуда партнёр принимает игроков?*\n\n" +
        "• 🌍 _Весь мир_ — без гео-ограничений\n" +
        "• 📍 _Только основная_ — как страна публикации\n" +
        "• ✍️ _Список_ — сам введёшь коды стран",
        [
          [{ text: "🌍 Весь мир", callback_data: `pacc:${draftId}:all` },
           { text: "📍 Только основная", callback_data: `pacc:${draftId}:only` }],
          [{ text: "✍️ Ввести список стран", callback_data: `pacc:${draftId}:list` }],
          [{ text: "← Назад к сводке", callback_data: `pshow:${draftId}` }],
        ]);
      return;
    }

    if (mode === "list") {
      await answerCallback(cb.id, env, "✍️ Жду список");
      const session = (await getPartnerSession(chatId, env)) ||
        { chat_id: chatId, started_at: new Date().toISOString() };
      session.stage = "edit_field";
      session.draft_id = draftId;
      session.edit_field = "acceptedCountries";
      await savePartnerSession(chatId, session, env);
      await sendMessage(chatId, env,
        "✍️ Пришли *коды стран через запятую* (ISO-2): напр. `ua, pl, de, kz`.",
        [[{ text: "← Отмена", callback_data: `pshow:${draftId}` }]]);
      return;
    }

    // all / only — применяем сразу
    const draft = await ghReadJSON(PARTNER_DRAFT_PATH(draftId), env);
    if (!draft) { await answerCallback(cb.id, env, "⚠️ Черновик не найден"); return; }
    draft.acceptedCountries = mode === "all"
      ? ["all"]
      : (draft.country ? [draft.country] : []);
    await ghWriteFile(PARTNER_DRAFT_PATH(draftId), JSON.stringify(draft, null, 2),
      `partner: acceptedCountries=${mode} for ${draftId}`, env);
    await answerCallback(cb.id, env, mode === "all" ? "🌍 Весь мир" : "📍 Только основная");
    await showPartnerDraftSummary(chatId, draftId, env);
    return;
  }

  if (action === "pcountry") {
    const draftId = segments[1];
    const code = segments[2];
    if (!validPartnerId(draftId) || !/^[a-z]{2}$/.test(code || "")) {
      await answerCallback(cb.id, env, "⚠️ Некорректные данные"); return;
    }
    await answerCallback(cb.id, env, `📍 Страна: ${code.toUpperCase()}`);
    await setPartnerCountry(chatId, draftId, code, cb.message, env);
    return;
  }

  if (action === "pmore") {
    const draftId = segments[1];   // может отсутствовать — не критично
    await answerCallback(cb.id, env, "✏️ Жду дополнение");
    const session = (await getPartnerSession(chatId, env)) ||
      { chat_id: chatId, started_at: new Date().toISOString() };
    session.stage = "await_more";
    if (validPartnerId(draftId)) session.draft_id = draftId;
    await savePartnerSession(chatId, session, env);
    await sendMessage(chatId, env,
      "✏️ Пришли *дополнение или исправление одним сообщением*. " +
      "Я учту его вместе с прежним описанием и пере-соберу черновик.");
    return;
  }

  if (action === "pconfirm") {
    const draftId = segments[1];
    if (!validPartnerId(draftId)) { await answerCallback(cb.id, env, "⚠️ Некорректный id"); return; }
    await answerCallback(cb.id, env, "🚀 Собираю страницу…");
    await editMessageRemoveButtons(cb.message, env, "ppreview");
    await confirmPartner(chatId, draftId, env);
    return;
  }

  if (action === "ppublish") {
    const draftId = segments[1];
    if (!validPartnerId(draftId)) { await answerCallback(cb.id, env, "⚠️ Некорректный id"); return; }
    await answerCallback(cb.id, env, "🌐 Публикую…");
    await editMessageRemoveButtons(cb.message, env, "ppublish");
    await publishPartner(chatId, draftId, env);
    return;
  }

  if (action === "pcancel") {
    await answerCallback(cb.id, env, "❌ Отменено");
    // Пытаемся подчистить черновик, если знаем id (из сессии).
    const session = await getPartnerSession(chatId, env);
    if (session && validPartnerId(session.draft_id)) {
      await ghDeleteFile(PARTNER_DRAFT_PATH(session.draft_id),
        "partner: cancel draft", env).catch(() => {});
    }
    await deletePartnerSession(chatId, env);
    await ghDeleteFile(PARTNER_INPUT_PATH(chatId), "partner: cancel input", env).catch(() => {});
    await editMessageText(cb.message,
      (cb.message.text || cb.message.caption || "") + "\n\n❌ *Отменено*", env);
    return;
  }
}

// ── Показать сводку по конкретному черновику + кнопки действий ──
async function showPartnerDraftSummary(chatId, draftId, env) {
  const draft = await ghReadJSON(PARTNER_DRAFT_PATH(draftId), env);
  if (!draft) {
    await sendMessage(chatId, env,
      "⚠️ Черновик ещё не готов или не найден. Подожди ~30 сек после отправки описания " +
      "и снова нажми «🔄 Проверить черновик».");
    return;
  }

  // Обновляем сессию: знаем id, ждём подтверждения.
  const session = (await getPartnerSession(chatId, env)) ||
    { chat_id: chatId, started_at: new Date().toISOString() };
  session.stage = "await_confirm";
  session.draft_id = draftId;
  await savePartnerSession(chatId, session, env);

  const summary = renderPartnerSummary(draft);

  // Если основная страна не определена — сначала просим выбрать её кнопками.
  const countryUnclear = draft._country_unclear || !draft.country;
  let kb;
  if (countryUnclear) {
    kb = [];
    // Раскладываем настроенные страны по 2 в ряд.
    for (let i = 0; i < PARTNER_COUNTRIES.length; i += 2) {
      const row = PARTNER_COUNTRIES.slice(i, i + 2).map(cc => ({
        text: `${COUNTRY_FLAGS[cc] || ""} ${cc.toUpperCase()}`,
        callback_data: `pcountry:${draftId}:${cc}`,
      }));
      kb.push(row);
    }
    kb.push([{ text: "❌ Отмена", callback_data: "pcancel" }]);
    await sendMessage(chatId, env,
      summary + "\n\n❓ *Укажи основную страну* (определяет путь страницы):", kb);
  } else {
    kb = [
      [{ text: "✅ Создать страницу", callback_data: `pconfirm:${draftId}` }],
      [{ text: "✏️ Исправить поле", callback_data: `pedit:${draftId}` },
       { text: "🌐 Принимает из…", callback_data: `pacc:${draftId}` }],
      [{ text: "➕ Дополнить текстом", callback_data: `pmore:${draftId}` }],
      [{ text: "❌ Отмена", callback_data: "pcancel" }],
    ];
    await sendMessage(chatId, env, summary, kb);
  }
}

// ── Найти свежайший черновик (когда авто-сводка не дошла) ──
async function showLatestPartnerDraft(chatId, env) {
  // 1) Если сессия уже знает id — показываем его.
  const session = await getPartnerSession(chatId, env);
  if (session && validPartnerId(session.draft_id)) {
    const draft = await ghReadJSON(PARTNER_DRAFT_PATH(session.draft_id), env);
    if (draft) { await showPartnerDraftSummary(chatId, session.draft_id, env); return; }
  }

  // 2) Иначе перебираем _partner_drafts/ и берём самый свежий по _parsed_at.
  const files = await ghListDir("_partner_drafts", env);
  const jsons = (files || []).filter(f => f.name.endsWith(".json"));
  if (jsons.length === 0) {
    await sendMessage(chatId, env,
      "⚠️ Черновиков пока нет — вероятно, парсинг ещё идёт или упал.\n\n" +
      "Проверь логи: *Actions → Parse partner description*. " +
      "Если упал (часто — нет `OPENROUTER_API_KEY`), поправь и пришли описание заново — жми «➕ Добавить партнёра» в меню");
    return;
  }

  let best = null;
  let bestTs = -1;
  for (const f of jsons) {
    const id = f.name.replace(/\.json$/, "");
    const d = await ghReadJSON(PARTNER_DRAFT_PATH(id), env);
    if (!d) continue;
    const ts = d._parsed_at ? new Date(d._parsed_at).getTime() : 0;
    if (ts >= bestTs) { bestTs = ts; best = id; }
  }
  if (!best) {
    await sendMessage(chatId, env, "⚠️ Не удалось прочитать черновики. Пришли описание заново — жми «➕ Добавить партнёра» в меню.");
    return;
  }
  await showPartnerDraftSummary(chatId, best, env);
}

// Резервный рендер сводки на стороне Worker'а. По основному пути сводку
// присылает parse_partner.py (send_telegram_summary). Эта версия нужна для
// pshow / pshow_latest / после выбора страны. Держим форматы согласованными.
function renderPartnerSummary(draft) {
  const L = ["📋 *Вот что я понял:*", ""];
  L.push(`🎯 *${escapeMd(draft.name || "?")}* · ${escapeMd(draft.type || "?")} · ${escapeMd(draft.networkLabel || draft.network || "?")}`);
  const country = draft.country;
  L.push(`📍 Основная страна: ${escapeMd(country || "не определена")} · Валюта: ${escapeMd(draft.currency || "?")}`);
  const accepted = draft.acceptedCountries || [];
  if (accepted.length && (accepted.length > 1 || (country && String(accepted) !== String([country])))) {
    L.push(`🌐 Принимает из: ${escapeMd(accepted.join(", "))}`);
  }
  L.push(`💰 Рейкбек: ${escapeMd(draft.rakeLabel || "?")}`);
  L.push(`⭐ KOZYR score: ${escapeMd(String(draft.score ?? "?"))}`);
  const games = draft.games || [];
  const limits = draft.limits || [];
  if (games.length || limits.length) {
    L.push(`🎮 ${escapeMd(limits.slice(0, 4).join(", "))} · ${escapeMd(games.join(", "))}`);
  }
  const sw = draft.software || [];
  if (sw.length) L.push(`📱 ${escapeMd(sw.join(", "))}`);
  const pros = draft.pros || [];
  const cons = draft.cons || [];
  L.push(`✅ Плюсы: ${pros.length} · ❌ Минусы: ${cons.length}`);
  const faq = draft.faq || [];
  if (faq.length) L.push(`❓ FAQ: ${faq.length}`);
  const missing = draft._missing || [];
  if (missing.length) {
    L.push("");
    L.push(`⚠️ _Не указано (будут дефолты): ${escapeMd(missing.slice(0, 8).join(", "))}_`);
  }
  L.push("");
  const kind = draft.type === "club" ? "clubs" : "rooms";
  L.push(`_Путь страницы: /${escapeMd(country || "??")}/${kind}/${escapeMd(draft.id || "?")}/_`);
  return L.join("\n");
}

// ── Проставить основную страну в черновике и показать сводку заново ──
async function setPartnerCountry(chatId, draftId, code, message, env) {
  if (!PARTNER_COUNTRIES.includes(code)) {
    await sendMessage(chatId, env,
      `⚠️ Страна \`${escapeMd(code)}\` не настроена в \`country_config.py\`. ` +
      `Доступны: ${PARTNER_COUNTRIES.map(c => "`" + c + "`").join(", ")}.`);
    return;
  }
  const draft = await ghReadJSON(PARTNER_DRAFT_PATH(draftId), env);
  if (!draft) {
    await sendMessage(chatId, env, "⚠️ Черновик не найден. Начни заново кнопкой «➕ Добавить партнёра» в меню");
    return;
  }
  draft.country = code;
  draft.countries = [code];
  draft._country_unclear = false;
  if (!Array.isArray(draft.acceptedCountries) || draft.acceptedCountries.length === 0) {
    draft.acceptedCountries = [code];
  }
  if (Array.isArray(draft._missing)) {
    draft._missing = draft._missing.filter(m => m !== "основная страна");
  }
  await ghWriteFile(PARTNER_DRAFT_PATH(draftId),
    JSON.stringify(draft, null, 2),
    `partner: set country ${code} for ${draftId}`, env);

  // Фиксируем выбор в исходном сообщении и присылаем обновлённую сводку.
  const base = (message.text || message.caption || "").split("\n\n❓")[0];
  await editMessageText(message, base + `\n\n📍 *Основная страна: ${code.toUpperCase()}*`, env);
  await showPartnerDraftSummary(chatId, draftId, env);
}

// ── Подтверждение: собрать страницу-превью (в _pending_partner/) ──
async function confirmPartner(chatId, draftId, env) {
  const draft = await ghReadJSON(PARTNER_DRAFT_PATH(draftId), env);
  if (!draft) {
    await sendMessage(chatId, env, "⚠️ Черновик не найден. Начни заново кнопкой «➕ Добавить партнёра» в меню");
    await deletePartnerSession(chatId, env);
    return;
  }
  // Страховка: не собираем страницу без основной страны (иначе уйдёт в /ua/ по дефолту).
  if (draft._country_unclear || !draft.country) {
    await sendMessage(chatId, env, "📍 Сначала выбери основную страну:");
    await showPartnerDraftSummary(chatId, draftId, env);
    return;
  }

  // Если логотип присылали ДО разбора анкеты — привяжем его сейчас.
  await attachStagedLogoIfAny(chatId, draftId, env);

  const ok = await triggerWorkflow("generate-partner-tpl.yml",
    { partner_id: draftId, publish: "false", chat_id: String(chatId) }, env);

  // Сессию НЕ удаляем: оставляем в стадии generated, чтобы оператор мог
  // дополнить/перегенерить или опубликовать. Закроется по TTL/pcancel/ppublish.
  const session = (await getPartnerSession(chatId, env)) ||
    { chat_id: chatId, started_at: new Date().toISOString() };
  session.stage = "generated";
  session.draft_id = draftId;
  await savePartnerSession(chatId, session, env);

  const kind = draft.type === "club" ? "clubs" : "rooms";
  const path = `/${draft.country}/${kind}/${draftId}/`;
  await sendMessage(chatId, env, ok
    ? `🚀 Собираю страницу партнёра *${escapeMd(draft.name || draftId)}*.\n\n` +
      `Через 2–3 минуты будет превью в \`_pending_partner/${escapeMd(draftId)}/index.html\`.\n` +
      `Прод-путь после публикации: \`${escapeMd(path)}\`\n\n` +
      `Проверь превью и, если всё ок, публикуй:`
    : "❌ Не удалось запустить генерацию (generate-partner-tpl.yml). Проверь GITHUB_TOKEN.",
    ok ? [
      [{ text: "🌐 Опубликовать в прод", callback_data: `ppublish:${draftId}` }],
      [{ text: "✏️ Дополнить и пересобрать", callback_data: `pmore:${draftId}` }],
      [{ text: "❌ Отмена", callback_data: "pcancel" }],
    ] : null);
}

// ── Публикация: страница в прод + партнёр в каталог ──
async function publishPartner(chatId, draftId, env) {
  const draft = await ghReadJSON(PARTNER_DRAFT_PATH(draftId), env);
  const ok = await triggerWorkflow("generate-partner-tpl.yml",
    { partner_id: draftId, publish: "true", chat_id: String(chatId) }, env);

  // Публикация — терминальный шаг: закрываем сессию и чистим ввод.
  await deletePartnerSession(chatId, env);
  await ghDeleteFile(PARTNER_INPUT_PATH(chatId), "partner: published, clear input", env).catch(() => {});

  const name = draft ? (draft.name || draftId) : draftId;
  await sendMessage(chatId, env, ok
    ? `🌐 Публикую *${escapeMd(name)}* в прод.\n\n` +
      `Workflow пересоберёт страницу, добавит партнёра в \`partners.json\` и \`partners.js\`. ` +
      `Через 2–3 минуты появится в каталоге и на карточке.`
    : "❌ Не удалось запустить публикацию (generate-partner-tpl.yml).");
}

// ── Хранение партнёрских сессий (как edit-сессии, через Contents API) ──
function validPartnerId(id) {
  return typeof id === "string" && /^[a-z0-9-]+$/.test(id);
}

async function getPartnerSession(chatId, env) {
  const data = await ghReadJSON(`.bot_state/partner_sessions/${chatId}.json`, env);
  if (!data) return null;
  const startedAt = new Date(data.started_at || 0).getTime();
  if (!startedAt || Date.now() - startedAt > PARTNER_SESSION_TTL_MS) {
    await deletePartnerSession(chatId, env);
    return null;
  }
  return data;
}

async function savePartnerSession(chatId, session, env) {
  await ghWriteFile(`.bot_state/partner_sessions/${chatId}.json`,
    JSON.stringify(session, null, 2),
    `partner session ${chatId} [${session.stage || "?"}]`, env);
}

async function deletePartnerSession(chatId, env) {
  await ghDeleteFile(`.bot_state/partner_sessions/${chatId}.json`,
    "close partner session", env).catch(() => {});
}

// ==== Suggested actions ====

async function handleSuggestedAction(action, row, cb, env) {
  // v2 multilang: gen_topic сразу запускает мультиязычную генерацию.
  // multilang-workflow принимает строку N через отдельный шаг dump-темы,
  // но чтобы не плодить workflow'ы — используем generate-from-row.yml,
  // который внутри вызывает multilang_generator.py (см. обновлённый workflow).
  const workflowMap = {
    approve_topic: "approve-topic.yml",
    reject_topic:  "reject-topic-row.yml",
    gen_topic:     "generate-from-row.yml",
  };
  const label = {
    approve_topic: "✅ В очередь",
    reject_topic:  "❌ Отклоняю",
    gen_topic:     "⚡ Запускаю генерацию",
  }[action];

  const ok = await triggerWorkflow(workflowMap[action], { row: String(row) }, env);
  if (!ok) {
    await answerCallback(cb.id, env, "❌ Не удалось запустить workflow");
    return;
  }
  await answerCallback(cb.id, env, label);

  // Помечаем сообщение как обработанное — убираем кнопки, приписываем строку
  const currentText = cb.message?.text || "";
  const statusLine = {
    approve_topic: "\n\n✅ *→ В очередь*",
    reject_topic:  "\n\n❌ *→ Отклонено*",
    gen_topic:     "\n\n⚡ *Генерация запущена*",
  }[action];
  await editMessageText(cb.message, currentText + statusLine, env);
}

// ==== Edit sessions (текстовые правки полей) ====

async function showEditFieldMenu(slug, message, env) {
  const fields = ["meta_title", "meta_description", "h1_title",
                  "image_prompt", "notes", "target_page"];
  const kb = fields.map(f => [{ text: f, callback_data: `edit:${slug}:${f}` }]);
  const text = `✏️ *Правка ${escapeMd(slug)}*\n\nВыбери поле для редактирования:`;
  await sendMessage(message.chat.id, env, text, kb);
}

async function startEditSession(slug, field, message, env) {
  const allowed = new Set(["meta_title", "meta_description", "h1_title",
                            "image_prompt", "notes", "target_page",
                            "primary_keyword", "secondary_keywords"]);
  if (!allowed.has(field)) {
    await sendMessage(message.chat.id, env,
      `⚠️ Поле \`${escapeMd(field)}\` не разрешено к правке из TG.\n` +
      `Разрешены: ${[...allowed].map(f => "`" + f + "`").join(", ")}`);
    return;
  }

  // Читаем текущее значение из meta.json в _pending/{slug}/
  const meta = await ghReadJSON(`_pending/${slug}/meta.json`, env);
  if (!meta) {
    await sendMessage(message.chat.id, env,
      `⚠️ \`_pending/${escapeMd(slug)}/meta.json\` не найден. ` +
      `Возможно, статья уже опубликована или отклонена.`);
    return;
  }
  const currentValue = meta[field] || "";

  // Сохраняем открытую сессию (через GitHub Contents API — чтобы Actions тоже видел)
  const session = {
    slug, field,
    opened_at: new Date().toISOString(),
    opened_by_chat_id: message.chat.id,
    opened_by_message_id: message.message_id,
    original_value: String(currentValue),
    proposed_value: null,
  };
  await ghWriteFile(
    `.bot_state/edit_sessions/${slug}.json`,
    JSON.stringify(session, null, 2),
    `open edit session ${slug}:${field}`,
    env,
  );

  const truncated = String(currentValue).slice(0, 500);
  const text = `✏️ *Редактирование ${escapeMd(slug)} · \`${escapeMd(field)}\`*\n\n` +
    `*Текущее значение:*\n\`\`\`\n${truncated}\n\`\`\`\n\n` +
    `Пришли *новое значение одним сообщением*. Оно заменит текущее в meta.json.\n` +
    `Отмена: /cancel`;
  await sendMessage(message.chat.id, env, text);
}

async function getOpenEditSessionForChat(chatId, env) {
  // Ходим в .bot_state/edit_sessions/ через Contents API, ищем ту, что открыта этим chat_id
  const files = await ghListDir(".bot_state/edit_sessions", env);
  if (!files) return null;
  for (const f of files) {
    if (!f.name.endsWith(".json")) continue;
    const data = await ghReadJSON(`.bot_state/edit_sessions/${f.name}`, env);
    if (!data) continue;
    if (data.opened_by_chat_id === chatId) {
      // TTL 30 минут
      const openedAt = new Date(data.opened_at).getTime();
      if (Date.now() - openedAt < 30 * 60 * 1000) {
        return data;
      }
    }
  }
  return null;
}

async function applyEditFromMessage(session, newValue, message, env) {
  const chatId = message.chat.id;
  const slug = session.slug;
  const field = session.field;
  // v2 multilang: sessionKey может быть "slug" (одноязычный) или
  // "slug__lang" (мультиязычный). lang задан только для мультиязычных сессий.
  const lang = session.lang || null;
  const sessionKey = session.session_key || slug;
  const pendingDir = lang ? (lang === "ru" ? "_pending" : `_pending_${lang}`)
                          : "_pending";
  const metaPath = `${pendingDir}/${slug}/meta.json`;

  // Читаем свежее meta.json (могло измениться другим коммитом)
  const meta = await ghReadJSON(metaPath, env);
  if (!meta) {
    await sendMessage(chatId, env,
      `⚠️ \`${metaPath}\` уже нет — вероятно, статья опубликована. Правка отменена.`);
    await ghDeleteFile(`.bot_state/edit_sessions/${sessionKey}.json`, "session cleanup", env);
    return;
  }

  const old = meta[field] || "";
  meta[field] = newValue;

  const langSuffix = lang ? ` [${lang}]` : "";
  const ok = await ghWriteFile(
    metaPath,
    JSON.stringify(meta, null, 2),
    `Edit${langSuffix} ${slug}: ${field}`,
    env,
  );
  if (!ok) {
    await sendMessage(chatId, env, "❌ Не удалось записать meta.json (GitHub Contents API).");
    return;
  }
  await ghDeleteFile(`.bot_state/edit_sessions/${sessionKey}.json`, "close edit session", env);
  await appendHistory(slug, "edit_applied", chatId,
    { field, old, new: newValue, lang }, env);

  const oldPreview = String(old).slice(0, 200);
  const newPreview = String(newValue).slice(0, 200);
  const langFlag = lang
    ? ({ru: "🇷🇺", uk: "🇺🇦", pl: "🇵🇱", kk: "🇰🇿"}[lang] || "") + ` \`${lang.toUpperCase()}\``
    : "";
  await sendMessage(chatId, env,
    `✅ *Правка применена* · ${escapeMd(slug)} ${langFlag} · \`${escapeMd(field)}\`\n\n` +
    `*Было:* \`${escapeMd(oldPreview)}\`\n` +
    `*Стало:* \`${escapeMd(newPreview)}\`\n\n` +
    `\`${metaPath}\` обновлён.`);
}

// ==== Отправка исходников генерации (sources) ====

async function sendSources(slug, message, env) {
  const chatId = message?.chat?.id;
  if (!chatId) return;

  const meta = await ghReadJSON(`_pending/${slug}/meta.json`, env);
  if (!meta) {
    await sendMessage(chatId, env, `⚠️ meta.json для \`${escapeMd(slug)}\` не найден.`);
    return;
  }

  // meta.topic_row_data — это то, что было в теме (что положено оператором в Sheets)
  const topic = meta.topic_row_data || {};
  const parts = [
    `🧾 *Исходники генерации* · \`${escapeMd(slug)}\``,
    ``,
    `*Тема (из Google Sheets):*`,
    `• topic: ${escapeMd(topic.topic || "?")}`,
    `• primary_keyword: \`${escapeMd(topic.primary_keyword || "?")}\``,
    `• secondary: ${escapeMd(topic.secondary_keywords || "?")}`,
    `• intent: ${escapeMd(topic.intent || "?")}`,
    `• target: \`${escapeMd(topic.target_page || "?")}\``,
    `• notes: ${escapeMd((topic.notes || "").slice(0, 300))}`,
    ``,
    `*Результат генерации:*`,
    `• slug: \`${escapeMd(meta.slug || "")}\``,
    `• url_slug: \`${escapeMd(meta.url_slug || meta.slug || "")}\``,
    `• meta_title: ${escapeMd(meta.meta_title || "")}`,
    `• meta_description: ${escapeMd(meta.meta_description || "")}`,
    `• h1_title: ${escapeMd(meta.h1_title || "")}`,
    `• word_count: ${meta.word_count || "?"}`,
    `• tags: ${escapeMd((meta.tags || []).join(", "))}`,
    `• image_prompt: ${escapeMd((meta.image_prompt || "").slice(0, 200))}`,
    `• has_hero_image: ${meta.has_hero_image ? "✅" : "❌"}`,
    `• generated_at: ${escapeMd(meta.generated_at || "")}`,
  ];
  await sendMessage(chatId, env, parts.join("\n"));

  // Если есть quality-отчёт — прикладываем
  const quality = await ghReadJSON(`_pending/${slug}/quality.json`, env);
  if (quality) {
    const qparts = [
      `📊 *Оценка качества*`,
      ``,
      `• Total: *${quality.total || "?"}*/100 · Verdict: *${escapeMd(quality.verdict || "?")}*`,
    ];
    if (quality.technical) {
      qparts.push(`• Technical: ${quality.technical.percent || "?"}%`);
    }
    if (quality.content) {
      qparts.push(`• Content: ${quality.content.percent || "?"}%`);
    }
    if (quality.warnings && quality.warnings.length) {
      qparts.push("");
      qparts.push("*Предупреждения:*");
      for (const w of quality.warnings.slice(0, 8)) {
        qparts.push(`  • ${escapeMd(String(w).slice(0, 200))}`);
      }
    }
    await sendMessage(chatId, env, qparts.join("\n"));
  }
}

// ==== GitHub API helpers ====

async function ghApi(path, method = "GET", body = null, env) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}${path}`;
  const init = {
    method,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kozyr-telegram-bot/2.0",
    },
  };
  if (body) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return await fetch(url, init);
}

async function triggerWorkflow(workflowFile, inputs, env) {
  const path = `/actions/workflows/${workflowFile}/dispatches`;
  try {
    const resp = await ghApi(path, "POST", { ref: "main", inputs }, env);
    if (resp.status === 204) return true;
    const txt = await resp.text();
    console.error(`Workflow dispatch failed (${resp.status}): ${txt}`);
    return false;
  } catch (e) {
    console.error("triggerWorkflow exception:", e);
    return false;
  }
}

async function ghListRuns(env) {
  try {
    const resp = await ghApi("/actions/runs?per_page=15", "GET", null, env);
    if (!resp.ok) return null;
    const data = await resp.json();
    return (data.workflow_runs || []).map(r => ({
      name: r.name,
      status: r.status,
      conclusion: r.conclusion,
      html_url: r.html_url,
      created_at: r.created_at,
    }));
  } catch (e) {
    return null;
  }
}

async function ghListDir(dirPath, env) {
  try {
    const resp = await ghApi(`/contents/${encodeURI(dirPath)}?ref=main`, "GET", null, env);
    if (!resp.ok) return null;
    const data = await resp.json();
    if (!Array.isArray(data)) return [];
    return data.map(x => ({ name: x.name, path: x.path, type: x.type }));
  } catch (e) {
    return null;
  }
}

async function ghReadFile(filePath, env) {
  try {
    const resp = await ghApi(`/contents/${encodeURI(filePath)}?ref=main`, "GET", null, env);
    if (!resp.ok) return null;
    const data = await resp.json();
    if (!data.content) return null;
    // atob возвращает бинарную строку; преобразуем в utf-8
    const binary = atob(data.content.replace(/\n/g, ""));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const text = new TextDecoder("utf-8").decode(bytes);
    return { text, sha: data.sha };
  } catch (e) {
    console.error("ghReadFile failed:", filePath, e);
    return null;
  }
}

async function ghReadJSON(filePath, env) {
  const f = await ghReadFile(filePath, env);
  if (!f) return null;
  try {
    return JSON.parse(f.text);
  } catch (e) {
    console.error("ghReadJSON parse failed:", filePath, e);
    return null;
  }
}

async function ghWriteFile(filePath, content, commitMessage, env) {
  // Читаем sha (нужен для update), либо создаём новый
  let sha = undefined;
  try {
    const readResp = await ghApi(`/contents/${encodeURI(filePath)}?ref=main`, "GET", null, env);
    if (readResp.ok) {
      const data = await readResp.json();
      sha = data.sha;
    }
  } catch (e) { /* Файла нет — это ок, будет создание */ }

  // btoa из UTF-8: сначала кодируем в bytes, потом base64
  const bytes = new TextEncoder().encode(content);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  const base64 = btoa(binary);

  const body = {
    message: commitMessage,
    content: base64,
    branch: "main",
  };
  if (sha) body.sha = sha;

  try {
    const resp = await ghApi(`/contents/${encodeURI(filePath)}`, "PUT", body, env);
    if (resp.ok) return true;
    const txt = await resp.text();
    console.error(`ghWriteFile ${filePath} failed (${resp.status}): ${txt.slice(0, 400)}`);
    return false;
  } catch (e) {
    console.error("ghWriteFile exception:", filePath, e);
    return false;
  }
}

async function ghDeleteFile(filePath, commitMessage, env) {
  try {
    const readResp = await ghApi(`/contents/${encodeURI(filePath)}?ref=main`, "GET", null, env);
    if (!readResp.ok) return true;   // Уже нет
    const data = await readResp.json();
    const sha = data.sha;
    const body = { message: commitMessage, sha, branch: "main" };
    const resp = await ghApi(`/contents/${encodeURI(filePath)}`, "DELETE", body, env);
    return resp.ok;
  } catch (e) {
    console.error("ghDeleteFile exception:", filePath, e);
    return false;
  }
}

async function appendHistory(slug, action, byChatId, details, env) {
  const path = `.bot_state/history/${slug}.json`;
  let history = await ghReadJSON(path, env);
  if (!Array.isArray(history)) history = [];
  history.push({
    at: new Date().toISOString(),
    action,
    by_chat_id: byChatId,
    details: details || {},
  });
  await ghWriteFile(path, JSON.stringify(history, null, 2),
    `history: ${slug} ${action}`, env);
}

// ==== Telegram helpers ====

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
    ppreview: "\n\n🚀 *Собираю страницу-превью...*",
    ppublish: "\n\n🌐 *Публикую в прод...*",
  }[action] || "";
  const newText = ((message.text || message.caption || "") + statusLine).slice(0, 4000);
  await editMessageText(message, newText, env);
}

async function editMessageText(message, newText, env) {
  const chatId = message.chat?.id;
  const messageId = message.message_id;
  if (!chatId || !messageId) return;
  // Если было фото — редактируем caption, если текст — editMessageText.
  const isPhoto = !message.text && !!message.caption;
  const endpoint = isPhoto ? "editMessageCaption" : "editMessageText";
  const payload = isPhoto
    ? { chat_id: chatId, message_id: messageId, caption: newText, parse_mode: "Markdown" }
    : { chat_id: chatId, message_id: messageId, text: newText, parse_mode: "Markdown",
        disable_web_page_preview: true };
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error(`${endpoint} failed:`, e);
  }
}

// v2 multilang: главная постоянная клавиатура снизу чата.
// Всегда видна оператору. Кнопки посылают ТЕКСТ (не callback_data)
// — то есть эквивалент команды. Так работают ReplyKeyboardMarkup.
const MAIN_MENU_KEYBOARD = {
  keyboard: [
    [{ text: "📝 Темы" }, { text: "⚡ Сгенерить" }],
    [{ text: "📊 Аналитика" }, { text: "📋 Pending" }],
    [{ text: "🌍 Страны" }, { text: "➕ Партнёр" }],
    [{ text: "⚙️ Ещё" }],
  ],
  resize_keyboard: true,
  is_persistent: true,
};

// Сопоставление текстов кнопок → команд
const BUTTON_TO_COMMAND = {
  "📝 Темы":        "/suggested",
  "⚡ Сгенерить":  "/generate",
  "📊 Аналитика":  "/analytics",
  "📋 Pending":    "/pending",
  "🌍 Страны":    "/countries",
  "➕ Партнёр":    "/addpartner",
  "⚙️ Ещё":       "__more_menu__",  // спец-маркер, откроет inline-меню
};

async function sendMessage(chatId, env, text, inlineKeyboard = null,
                            replyKeyboard = null) {
  const payload = {
    chat_id: chatId,
    text: String(text).slice(0, 4000),
    parse_mode: "Markdown",
    disable_web_page_preview: true,
  };
  if (inlineKeyboard) {
    payload.reply_markup = { inline_keyboard: inlineKeyboard };
  } else if (replyKeyboard) {
    payload.reply_markup = replyKeyboard;
  }
  try {
    const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const body = await resp.text();
      console.error(`sendMessage ${resp.status}: ${body.slice(0, 300)}`);
      // Fallback: если Markdown сломался, шлём plain
      if (resp.status === 400 && /parse entities/i.test(body)) {
        payload.parse_mode = undefined;
        const r2 = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        try {
          const j2 = await r2.json();
          return j2?.result?.message_id || null;
        } catch (_) { return null; }
      }
      return null;
    }
    // Успех — вернём message_id, чтобы вызывающий мог его запомнить/удалить.
    try {
      const j = await resp.json();
      return j?.result?.message_id || null;
    } catch (_) {
      return null;
    }
  } catch (e) {
    console.error("sendMessage failed:", e);
    return null;
  }
}

// Удаляет сообщение по id. Тихо игнорирует ошибки (сообщение могло быть
// удалено вручную, или ему >48ч — Telegram такие удалять не даёт).
async function deleteMessage(chatId, messageId, env) {
  if (!chatId || !messageId) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/deleteMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, message_id: messageId }),
    });
  } catch (e) {
    // не критично — просто лог
    console.error("deleteMessage failed:", e);
  }
}

// v2 multilang: определяет каталог _pending по языку.
// Соответствует LANG_CONFIG в lang_config.py:
//   "ru" → "_pending"           (первичный, исторический)
//   "uk" → "_pending_uk"
//   любой другой → "_pending_{lang}"
function pendingDirForLang(lang) {
  if (!lang || lang === "ru") return "_pending";
  return `_pending_${lang}`;
}

async function sendFullTextForLang(slug, lang, message, env) {
  const chatId = message?.chat?.id;
  if (!chatId) return;
  const dir = pendingDirForLang(lang);
  const f = await ghReadFile(`${dir}/${slug}/body.md`, env);
  if (!f) {
    await sendMessage(chatId, env,
      `⚠️ Не удалось загрузить \`${dir}/${slug}/body.md\`. ` +
      `Возможно эта языковая версия ещё не создана.`);
    return;
  }
  const body = f.text;
  const CHUNK = 3800;
  const total = Math.ceil(body.length / CHUNK);
  const langFlag = {ru: "🇷🇺", uk: "🇺🇦", pl: "🇵🇱", kk: "🇰🇿"}[lang] || "";
  for (let i = 0; i < total; i++) {
    const part = body.slice(i * CHUNK, (i + 1) * CHUNK);
    const header = total > 1
      ? `📄 *Полный текст ${langFlag} ${lang.toUpperCase()}* (${i + 1}/${total})\n\n`
      : `📄 *Полный текст ${langFlag} ${lang.toUpperCase()}*\n\n`;
    const payload = {
      chat_id: chatId,
      text: (header + part).slice(0, 4000),
      disable_web_page_preview: true,
    };
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }
}

async function showEditFieldMenuForLang(slug, lang, message, env) {
  const fields = ["meta_title", "meta_description", "h1_title",
                  "image_prompt", "notes", "target_page"];
  const kb = fields.map(f => [{
    text: f,
    callback_data: `edit_lang:${lang}:${slug}:${f}`,
  }]);
  const langFlag = {ru: "🇷🇺", uk: "🇺🇦", pl: "🇵🇱", kk: "🇰🇿"}[lang] || "";
  const text = `✏️ *Правка ${escapeMd(slug)}* ${langFlag} \`${lang.toUpperCase()}\`\n\n` +
    `Выбери поле для редактирования:`;
  await sendMessage(message.chat.id, env, text, kb);
}

async function startEditSessionForLang(slug, lang, field, message, env) {
  const allowed = new Set(["meta_title", "meta_description", "h1_title",
                            "image_prompt", "notes", "target_page",
                            "primary_keyword", "secondary_keywords"]);
  if (!allowed.has(field)) {
    await sendMessage(message.chat.id, env,
      `⚠️ Поле \`${escapeMd(field)}\` не разрешено к правке из TG.`);
    return;
  }
  const dir = pendingDirForLang(lang);
  const meta = await ghReadJSON(`${dir}/${slug}/meta.json`, env);
  if (!meta) {
    await sendMessage(message.chat.id, env,
      `⚠️ \`${dir}/${slug}/meta.json\` не найден.`);
    return;
  }
  const currentValue = meta[field] || "";

  // Ключ сессии включает язык, чтобы правки разных языков одной статьи
  // не конфликтовали
  const sessionKey = `${slug}__${lang}`;
  const session = {
    slug, lang, field,
    session_key: sessionKey,
    opened_at: new Date().toISOString(),
    opened_by_chat_id: message.chat.id,
    opened_by_message_id: message.message_id,
    original_value: String(currentValue),
    proposed_value: null,
  };
  await ghWriteFile(
    `.bot_state/edit_sessions/${sessionKey}.json`,
    JSON.stringify(session, null, 2),
    `open edit session ${sessionKey}:${field}`,
    env,
  );

  const truncated = String(currentValue).slice(0, 500);
  const langFlag = {ru: "🇷🇺", uk: "🇺🇦", pl: "🇵🇱", kk: "🇰🇿"}[lang] || "";
  const text = `✏️ *Правка ${escapeMd(slug)}* ${langFlag} \`${lang.toUpperCase()}\` · \`${escapeMd(field)}\`\n\n` +
    `*Текущее значение:*\n\`\`\`\n${truncated}\n\`\`\`\n\n` +
    `Пришли *новое значение одним сообщением*. Оно заменит текущее в \`${dir}/${slug}/meta.json\`.\n` +
    `Отмена: /cancel`;
  await sendMessage(message.chat.id, env, text);
}

async function sendFullText(slug, message, env) {
  const chatId = message?.chat?.id;
  if (!chatId) return;
  const f = await ghReadFile(`_pending/${slug}/body.md`, env);
  if (!f) {
    await sendMessage(chatId, env, "⚠️ Не удалось загрузить текст статьи");
    return;
  }
  const body = f.text;
  const CHUNK = 3800;
  const total = Math.ceil(body.length / CHUNK);
  for (let i = 0; i < total; i++) {
    const part = body.slice(i * CHUNK, (i + 1) * CHUNK);
    const header = total > 1 ? `📄 *Полный текст* (${i + 1}/${total})\n\n` : "📄 *Полный текст*\n\n";
    // Тело — plain text, чтобы Markdown в статье не ломал парсер
    const payload = {
      chat_id: chatId,
      text: (header + part).slice(0, 4000),
      disable_web_page_preview: true,
    };
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  ЛОГОТИП ПАРТНЁРА: приём фото/картинки в Telegram → файл в репозиторий →
//  привязка к черновику (draft.logo_img). Конвенция путей — как у существующих
//  партнёров: ua/blog/logos/{id}.{ext}, а в карточке /ua/blog/logos/{id}.{ext}.
// ═══════════════════════════════════════════════════════════════════════════

// Приём логотипа во время партнёрской сессии.
async function handlePartnerLogo(chatId, message, env) {
  const session = await getPartnerSession(chatId, env);
  if (!session) return;

  // 1. file_id: фото (берём самое большое) или документ-картинку.
  let fileId = null;
  let hintName = "";
  if (Array.isArray(message.photo) && message.photo.length) {
    fileId = message.photo[message.photo.length - 1].file_id;
    hintName = "photo.jpg";
  } else if (message.document) {
    const mime = message.document.mime_type || "";
    if (!/^image\//.test(mime)) {
      await sendMessage(chatId, env,
        "⚠️ Это не картинка. Пришли логотип как фото или файл-изображение (PNG/JPG/WebP/SVG).");
      return;
    }
    fileId = message.document.file_id;
    hintName = message.document.file_name || "logo";
  }
  if (!fileId) {
    await sendMessage(chatId, env, "⚠️ Не нашёл картинку в сообщении.");
    return;
  }

  // 2. getFile → путь на серверах Telegram.
  const tgPath = await tgGetFile(fileId, env);
  if (!tgPath) {
    await sendMessage(chatId, env, "❌ Не удалось получить файл из Telegram (getFile).");
    return;
  }
  const ext = imageExt(tgPath) || imageExt(hintName) || "jpg";

  // 3. Скачиваем байты и кодируем в base64.
  let buf;
  try {
    buf = await tgDownloadFile(tgPath, env);
  } catch (e) {
    await sendMessage(chatId, env, "❌ Не удалось скачать файл из Telegram.");
    return;
  }
  const base64 = arrayBufferToBase64(buf);

  // 4a. Партнёр уже известен — сохраняем СЫРОЙ файл и запускаем авто-обработку
  // (обрезка полей, квадрат, webp). logo_img проставляем оптимистично как .webp:
  // авто-обработка создаст именно его, а генерация страницы сразу берёт верный src.
  if (validPartnerId(session.draft_id)) {
    const id = session.draft_id;
    const rawPath = `_partner_logos_raw/${id}.${ext}`;
    const okImg = await ghWriteBinary(rawPath, base64, `partner: raw logo for ${id}`, env);
    if (!okImg) {
      await sendMessage(chatId, env, "❌ Не удалось сохранить логотип в репозиторий.");
      return;
    }
    const draft = await ghReadJSON(PARTNER_DRAFT_PATH(id), env);
    if (draft) {
      draft.logo_img = `/ua/blog/logos/${id}.webp`;
      if (Array.isArray(draft._missing)) {
        draft._missing = draft._missing.filter(m => !/logo/i.test(m));
      }
      await ghWriteFile(PARTNER_DRAFT_PATH(id),
        JSON.stringify(draft, null, 2), `partner: logo path ${id}`, env);
    }
    const ok = await triggerWorkflow("process-logo.yml", { partner_id: id }, env);
    await sendMessage(chatId, env, ok
      ? `✅ Логотип принят · *${escapeMd((draft && draft.name) || id)}*.\n\n` +
        `Обрабатываю: обрезаю поля, делаю квадрат и конвертирую в webp… ` +
        `через ~1 минуту будет готов и привязан. Если превью уже собрано — ` +
        `потом нажми «✏️ Дополнить и пересобрать».`
      : `⚠️ Логотип сохранён, но не удалось запустить обработку (process-logo.yml). ` +
        `Проверь, что воркфлоу залит в .github/workflows/.`);
    return;
  }

  // 4b. Партнёр ещё не разобран — стейджим логотип под чат до появления черновика.
  const stagePath = `.bot_state/partner_logos/${chatId}.${ext}`;
  const okStage = await ghWriteBinary(stagePath, base64,
    `partner: staged logo for chat ${chatId}`, env);
  if (!okStage) {
    await sendMessage(chatId, env, "❌ Не удалось сохранить логотип.");
    return;
  }
  session.logo_staged_ext = ext;
  await savePartnerSession(chatId, session, env);
  await sendMessage(chatId, env,
    "✅ Логотип принял. Привяжу его к партнёру, как только разберу анкету " +
    "(пришли описание текстом, если ещё не присылал).");
}

// Перенести застейдженный логотип на партнёра (перед сборкой превью).
async function attachStagedLogoIfAny(chatId, draftId, env) {
  const session = await getPartnerSession(chatId, env);
  if (!session || !session.logo_staged_ext) return;
  if (!validPartnerId(draftId)) return;
  const ext = session.logo_staged_ext;
  const stagePath = `.bot_state/partner_logos/${chatId}.${ext}`;
  const staged = await ghReadFileBase64(stagePath, env);
  if (!staged) return;
  const rawPath = `_partner_logos_raw/${draftId}.${ext}`;
  const ok = await ghWriteBinary(rawPath, staged, `partner: raw logo for ${draftId}`, env);
  if (!ok) return;
  const draft = await ghReadJSON(PARTNER_DRAFT_PATH(draftId), env);
  if (draft && !draft.logo_img) {
    draft.logo_img = `/ua/blog/logos/${draftId}.webp`;
    if (Array.isArray(draft._missing)) {
      draft._missing = draft._missing.filter(m => !/logo/i.test(m));
    }
    await ghWriteFile(PARTNER_DRAFT_PATH(draftId),
      JSON.stringify(draft, null, 2), `partner: logo path ${draftId}`, env);
  }
  await triggerWorkflow("process-logo.yml", { partner_id: draftId }, env);
  await ghDeleteFile(stagePath, "partner: clear staged logo", env).catch(() => {});
  delete session.logo_staged_ext;
  await savePartnerSession(chatId, session, env);
}

// ── Telegram file helpers ──
async function tgGetFile(fileId, env) {
  try {
    const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getFile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: fileId }),
    });
    const j = await r.json();
    return j?.result?.file_path || null;
  } catch (e) {
    console.error("tgGetFile failed:", e);
    return null;
  }
}

async function tgDownloadFile(filePath, env) {
  const r = await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${filePath}`);
  if (!r.ok) throw new Error(`download ${r.status}`);
  return await r.arrayBuffer();
}

function imageExt(nameOrPath) {
  const m = /\.(png|jpe?g|webp|svg|gif)$/i.exec(nameOrPath || "");
  if (!m) return null;
  let e = m[1].toLowerCase();
  if (e === "jpeg") e = "jpg";
  return e;
}

function arrayBufferToBase64(buf) {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

// ── GitHub бинарные helpers (логотип) ──
// Запись бинарника: content — уже base64 сырых байт (не перекодируем как текст).
async function ghWriteBinary(filePath, base64Content, commitMessage, env) {
  let sha = undefined;
  try {
    const readResp = await ghApi(`/contents/${encodeURI(filePath)}?ref=main`, "GET", null, env);
    if (readResp.ok) { const d = await readResp.json(); sha = d.sha; }
  } catch (e) { /* нет файла — создаём */ }
  const body = { message: commitMessage, content: base64Content, branch: "main" };
  if (sha) body.sha = sha;
  try {
    const resp = await ghApi(`/contents/${encodeURI(filePath)}`, "PUT", body, env);
    if (resp.ok) return true;
    const t = await resp.text();
    console.error(`ghWriteBinary ${filePath} failed (${resp.status}): ${t.slice(0, 300)}`);
    return false;
  } catch (e) {
    console.error("ghWriteBinary exception:", filePath, e);
    return false;
  }
}

// Читает файл и возвращает СЫРОЙ base64 (для переноса бинарника без перекодировки).
async function ghReadFileBase64(filePath, env) {
  try {
    const resp = await ghApi(`/contents/${encodeURI(filePath)}?ref=main`, "GET", null, env);
    if (!resp.ok) return null;
    const data = await resp.json();
    if (!data.content) return null;
    return data.content.replace(/\n/g, "");
  } catch (e) {
    console.error("ghReadFileBase64 failed:", filePath, e);
    return null;
  }
}

function escapeMd(text) {
  if (!text) return "";
  return String(text)
    .replace(/\\/g, "\\\\")
    .replace(/_/g, "\\_")
    .replace(/\*/g, "\\*")
    .replace(/`/g, "\\`");
}
