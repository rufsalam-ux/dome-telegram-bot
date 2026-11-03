import { webMethod, Permissions } from 'wix-web-module';
import { currentMember } from 'wix-members-backend';
import { secrets } from 'wix-secrets-backend.v2';
import { elevate } from 'wix-auth';
import { fetch } from 'wix-fetch';

// Exact school endpoint; callers cannot supply a URL, member ID, or chat ID.
const ENDPOINT = 'https://admin.bilinguadom.com/school/telegram/';
async function request(action) {
  const member = await currentMember.getMember({ fieldsets: ['FULL'] });
  if (!member?._id || member.status !== 'APPROVED') throw new Error('AUTH_REQUIRED');
  try {
    const { value } = await elevate(secrets.getSecretValue)('SCHOOL_BRIDGE_KEY');
    if (!value || value.length < 43) throw new Error('CONFIGURATION_REQUIRED');
    const response = await fetch(ENDPOINT + action, {
      method: 'post',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + value },
      body: JSON.stringify({ memberId: member._id })
    });
    if (!response.ok) throw new Error('SERVICE_UNAVAILABLE');
    const result = await response.json();
    if (action === 'link') {
      if (!/^https:\/\/t\.me\/BilingvaDom_bot\?start=school_[A-Za-z0-9_-]{43}$/.test(result.url || '')) throw new Error('INVALID_RESPONSE');
      return { url: result.url, expiresIn: 600 };
    }
    if (typeof result.linked !== 'boolean') throw new Error('INVALID_RESPONSE');
    return { linked: result.linked, botUsername: 'BilingvaDom_bot' };
  } catch (_) {
    return { error: 'TELEGRAM_UNAVAILABLE' };
  }
}
export const getTelegramStatus = webMethod(Permissions.SiteMember, () => request('status'));
export const connectTelegram = webMethod(Permissions.SiteMember, () => request('link'));
export const disconnectTelegram = webMethod(Permissions.SiteMember, () => request('unlink'));
