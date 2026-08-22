// Cloudflare Pages Function
// Перехватывает запрос /sitemap.xml и отдаёт содержимое sitemap-raw.xml
// с ГАРАНТИРОВАННЫМ Content-Type: application/xml.
// Причина: Cloudflare Pages по умолчанию отдаёт .xml как text/html,
// что ломает парсинг в Google Search Console. Функция решает это надёжно.
export async function onRequest(context) {
  const url = new URL(context.request.url);
  const res = await context.env.ASSETS.fetch(
    new URL("/sitemap-raw.xml", url.origin)
  );

  // Если по какой-то причине raw-файл недоступен — вернём 500, а не битый ответ
  if (!res.ok) {
    return new Response("sitemap source unavailable", { status: 500 });
  }

  const body = await res.text();
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
