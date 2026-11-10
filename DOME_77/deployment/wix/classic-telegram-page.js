import { getTelegramStatus, connectTelegram, disconnectTelegram } from 'backend/schoolTelegram.web';

$w.onReady(() => {
  const widget = $w('#html1');
  let busy = false;
  const send = data => widget.postMessage({ channel: 'school-telegram-v1', ...data });
  widget.onMessage(async event => {
    const message = event.data;
    if (!message || message.channel !== 'school-telegram-v1' || busy) return;
    const actions = { status: getTelegramStatus, connect: connectTelegram, disconnect: disconnectTelegram };
    if (!Object.prototype.hasOwnProperty.call(actions, message.action)) return;
    busy = true;
    try {
      // Never forward client-supplied identity or other parameters.
      const result = await actions[message.action]();
      if (result.error) throw new Error('UNAVAILABLE');
      send({ type: 'result', action: message.action, ...result });
    } catch (_) {
      send({ type: 'error', message: 'Подключение Telegram пока недоступно. Проверьте вход в кабинет и попробуйте позже.' });
    } finally {
      busy = false;
    }
  });
});
