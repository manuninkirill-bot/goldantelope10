/**
 * Cloudflare Worker — прокси для Telegram Bot API
 * Деплой: https://workers.cloudflare.com (бесплатно, 100k req/day)
 *
 * Инструкция:
 * 1. Зайдите на https://workers.cloudflare.com
 * 2. Create a Service → назовите, например, tg-proxy
 * 3. Вставьте весь этот код в редактор
 * 4. Нажмите Deploy
 * 5. Скопируйте URL воркера (вида https://tg-proxy.YOUR_NAME.workers.dev)
 * 6. В HF Space Settings → Variables → добавьте:
 *    TELEGRAM_API_BASE = https://tg-proxy.YOUR_NAME.workers.dev
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Проксируем на api.telegram.org
    const targetUrl = 'https://api.telegram.org' + url.pathname + url.search;

    // Пересылаем запрос с теми же методом, заголовками и телом
    const proxyRequest = new Request(targetUrl, {
      method: request.method,
      headers: request.headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
      redirect: 'follow',
    });

    try {
      const response = await fetch(proxyRequest);

      // Возвращаем ответ с CORS заголовками
      const newHeaders = new Headers(response.headers);
      newHeaders.set('Access-Control-Allow-Origin', '*');

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newHeaders,
      });
    } catch (err) {
      return new Response(JSON.stringify({ ok: false, error: err.message }), {
        status: 502,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  },
};
