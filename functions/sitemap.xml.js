// Cloudflare Pages Function
// Перехватывает запрос /sitemap.xml и отдаёт содержимое sitemap-raw.xml
// с ГАРАНТИРОВАННЫМ Content-Type: application/xml.
// Причина: Cloudflare Pages по умолчанию отдаёт .xml как text/html,
// и файл _headers этот тип не переопределяет. Функция решает это надёжно.
export async function onRequest(context) {
  const url = new URL(context.request.url);
  const res = await context.env.ASSETS.fetch(
    new URL("/sitemap-raw.xml", url.origin)
  );
  const body = await res.text();
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
      "X-Robots-Tag": "noindex",
    },
  });
}
