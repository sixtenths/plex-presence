const $ = id => document.getElementById(id);
let csrf = '', initialized = false, refreshing = false, pollingPin = false, quitting = false;
function message(text, error = false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; $('message').hidden = !text; }
async function post(path, data = {}) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'The request failed.');
  return value;
}
async function perform(button, task) {
  if (button) button.disabled = true;
  message('');
  try { await task(); await refresh(true); }
  catch (error) { message(error.message || 'Connection failed. Try again.', true); }
  finally { if (button) button.disabled = false; }
}
function fillSettings(s) {
  $('use-plex-artwork').checked = s.use_plex_artwork === true;
  $('username').value = s.plex_username || '';
  $('manual-user').value = s.plex_username || '';
  $('client-id').value = s.discord_client_id || '';
  for (const [id, key] of [['enabled','enabled'],['artwork-enabled','artwork_enabled'],['show-progress','show_progress']]) $(id).checked = s[key] !== false;
}
async function refresh(fill = false) {
  if (refreshing || quitting) return;
  refreshing = true;
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    if (!response.ok) throw new Error();
    const value = await response.json();
    csrf = value.csrf;
    const s = value.settings;
    if (!initialized || fill) { fillSettings(s); initialized = true; }
    $('redirect').textContent = value.redirect_uri;
    $('plex-account').textContent = value.plex_connected ? `Connected · ${s.plex_username || 'Plex account'}` : 'Sign in securely at Plex.';
    $('plex-connect').textContent = value.plex_connected ? 'Switch account ↗' : 'Connect Plex ↗';
    $('plex-disconnect').hidden = !value.plex_connected;
    $('pin-pending').hidden = !value.plex_signin_pending;
    $('chosen-server').textContent = s.server_name ? `Selected: ${s.server_name} · ${s.plex_url}` : 'No server selected.';
    $('discord-connected').hidden = !value.discord_connected;
    $('discord-setup').hidden = value.discord_connected;
    const plexLabels = {setup_required:'Choose a server',playing:'Playing',paused:'Paused · hidden',idle:'Waiting for playback',buffering:'Buffering · hidden',unavailable:'Server unavailable',disabled:'Sharing disabled'};
    $('plex-status').textContent = plexLabels[value.plex] || value.plex;
    $('discord-status').textContent = value.discord.message || value.discord.state;
    $('plex-dot').className = value.plex === 'playing' ? 'good' : (value.plex === 'unavailable' ? 'warn' : '');
    $('discord-dot').className = ['active','idle'].includes(value.discord.state) ? 'good' : '';
    const a = value.activity;
    $('live-badge').textContent = a && value.discord.state === 'active' ? 'Sharing now' : 'Not sharing';
    $('watching').textContent = a ? (a.type === 2 ? 'Listening on Plex' : 'Watching on Plex') : 'Ready when you are';
    $('media-title').textContent = a?.details || 'Your next great watch';
    $('media-state').textContent = a?.state || (value.plex === 'paused' ? 'Playback is paused. Your Discord activity is hidden.' : 'Start playing something on your selected server.');
    const image = a?.assets?.large_image;
    const safeImage = typeof image === 'string' && image.startsWith('https://wsrv.nl/?');
    $('poster').hidden = !safeImage; $('poster-placeholder').hidden = safeImage;
    if (safeImage && $('poster').src !== image) $('poster').src = image;
    if (!safeImage) $('poster').removeAttribute('src');
    if (value.auth_error) message(value.auth_error, true);
    if (value.plex_signin_pending && !pollingPin) void checkPin();
  } catch { if (!quitting) message('Cannot reach Plex Presence. Start the app again or reconnect your SSH tunnel.', true); }
  finally { refreshing = false; }
}
async function servers() {
  const result = await post('/api/plex/servers');
  $('servers').replaceChildren(new Option(result.servers.length ? 'Choose a server' : 'No accessible servers found', ''));
  for (const server of result.servers) $('servers').add(new Option(`${server.name}${server.owned ? '' : ' (shared)'}`, server.id));
}
async function checkPin() {
  if (pollingPin) return;
  pollingPin = true;
  try { const result = await post('/api/plex/poll'); if (result.connected) { await servers(); await refresh(true); message('Plex connected. Choose your server below.'); } }
  catch(error) { message(error.message, true); }
  finally { pollingPin = false; }
}
$('plex-connect').onclick = e => perform(e.currentTarget, async () => { location.assign((await post('/api/plex/connect')).url); });
$('plex-check').onclick = () => checkPin();
$('refresh-servers').onclick = e => perform(e.currentTarget, servers);
$('server-form').onsubmit = e => { e.preventDefault(); perform($('select-server'), async () => { await post('/api/plex/select', {server_id:$('servers').value}); message('Server connected. Continue with Discord.'); }); };
$('manual-form').onsubmit = e => { e.preventDefault(); perform(e.submitter, async () => { await post('/api/plex/manual',{url:$('server-url').value,token:$('plex-token').value,username:$('manual-user').value}); $('plex-token').value = ''; message('Server connected.'); }); };
$('discord-form').onsubmit = e => { e.preventDefault(); perform(e.submitter, async () => { location.assign((await post('/api/discord/connect', {client_id:$('client-id').value})).url); }); };
$('copy-redirect').onclick = e => perform(e.currentTarget, async () => { await navigator.clipboard.writeText($('redirect').textContent); message('Redirect URL copied. Paste it into your Discord application’s OAuth2 settings.'); });
$('refresh-users').onclick = e => perform(e.currentTarget, async () => { const result = await post('/api/plex/users'); $('users').replaceChildren(...result.users.map(name => new Option(name,name))); message('Current Plex users loaded. Choose one or enter a username.'); });
$('settings-form').onsubmit = e => { e.preventDefault(); perform(e.submitter, async () => { await post('/api/settings',{plex_username:$('username').value,enabled:$('enabled').checked,artwork_enabled:$('artwork-enabled').checked,use_plex_artwork:$('use-plex-artwork').checked,show_progress:$('show-progress').checked}); message('Preferences saved.'); }); };
for (const provider of ['plex','discord']) $(`${provider}-disconnect`).onclick = e => perform(e.currentTarget, async () => { await post(`/api/${provider}/disconnect`); message(`${provider === 'plex' ? 'Plex' : 'Discord'} disconnected from this installation.`); });
$('quit').onclick = e => perform(e.currentTarget, async () => { await post('/api/quit'); quitting = true; message('Plex Presence has stopped. You can close this tab.'); });
$('poster').onerror = () => { $('poster').hidden = true; $('poster-placeholder').hidden = false; };
refresh().then(async () => { if (new URLSearchParams(location.search).has('plex')) await checkPin(); });
setInterval(() => refresh(), 5000);
