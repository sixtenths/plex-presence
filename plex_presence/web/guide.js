(() => {
  let dialog, pages, position = 0;
  const seenKey = 'plex-presence-guide-v1';
  function showPage(index) {
    position = Math.max(0, Math.min(index, pages.length - 1));
    pages.forEach((page, i) => { page.hidden = i !== position; });
    dialog.querySelectorAll('[data-guide-go]').forEach((button, i) => button.setAttribute('aria-current', i === position ? 'step' : 'false'));
    dialog.querySelector('#guide-count').textContent = `Step ${position + 1} of ${pages.length}`;
    dialog.querySelector('#guide-back').disabled = position === 0;
    dialog.querySelector('#guide-next').textContent = position === pages.length - 1 ? 'Finish & open setup' : 'Next step →';
    dialog.querySelector('.guide-body').scrollTop = 0;
    const heading = pages[position].querySelector('h2');
    heading.tabIndex = -1; heading.focus();
  }
  function dismiss() {
    try { localStorage.setItem(seenKey, 'yes'); } catch {}
    dialog.close(); document.getElementById('open-guide').focus();
  }
  async function openGuide() {
    if (!dialog) {
      const response = await fetch('/guide.html');
      if (!response.ok) throw new Error('Could not open the guide. Restart Plex Presence and try again.');
      const template = document.createElement('template');
      template.innerHTML = await response.text();
      document.body.append(template.content);
      dialog = document.getElementById('setup-guide');
      pages = [...dialog.querySelectorAll('[data-guide-section]')];
      dialog.querySelectorAll('[data-guide-go]').forEach(button => button.onclick = () => showPage(Number(button.dataset.guideGo)));
      dialog.querySelector('#guide-close').onclick = dismiss;
      dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); });
      dialog.querySelector('#guide-back').onclick = () => showPage(position - 1);
      dialog.querySelector('#guide-next').onclick = () => position === pages.length - 1 ? dismiss() : showPage(position + 1);
      dialog.querySelectorAll('[data-guide-redirect]').forEach(el => el.textContent = location.origin + '/callback');
      dialog.querySelectorAll('[data-guide-local]').forEach(el => el.textContent = location.origin);
    }
    if (!dialog.open) dialog.showModal();
    showPage(position);
  }
  document.getElementById('open-guide').onclick = () => openGuide().catch(error => alert(error.message));
  let first = true;
  try { first = !localStorage.getItem(seenKey); } catch {}
  if (first) openGuide().catch(() => {});
})();
