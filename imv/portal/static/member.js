const message = document.querySelector('#message');
async function api(url, options = {}) {
  const response = await fetch(url, {credentials: 'same-origin', ...options});
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `HTTP ${response.status}`); }
  return response;
}
const register = document.querySelector('#register');
if (register) register.addEventListener('submit', async event => {
  event.preventDefault(); const data = Object.fromEntries(new FormData(register));
  data.terms_accepted = register.terms_accepted.checked; data.privacy_accepted = register.privacy_accepted.checked;
  try { await api('/api/member/register', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)}); message.textContent='인증메일을 보냈습니다. 메일의 링크를 눌러주세요.'; register.reset(); }
  catch (error) { message.textContent=error.message; }
});
const login = document.querySelector('#login');
if (login) login.addEventListener('submit', async event => {
  event.preventDefault(); const data = Object.fromEntries(new FormData(login));
  try { await api('/api/member/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)}); location.href='/member/releases'; }
  catch (error) { message.textContent=error.message; }
});
const releases = document.querySelector('#releases');
if (releases) api('/api/releases').then(r => r.json()).then(items => {
  releases.replaceChildren(...items.map(item => {
    const box=document.createElement('div'); box.className='release';
    const title=document.createElement('h2'); title.textContent=`v${item.version}`;
    const notes=document.createElement('p'); notes.textContent=item.notes;
    const hash=document.createElement('code'); hash.textContent=`SHA-256 ${item.sha256}`;
    const button=document.createElement('button'); button.textContent='설치본 다운로드';
    button.onclick=async()=>{ try { const r=await api(`/api/download/${encodeURIComponent(item.version)}`,{method:'POST'}); const blob=await r.blob(); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=item.file; a.click(); URL.revokeObjectURL(a.href); } catch(e) { message.textContent=e.message; } };
    box.append(title,notes,hash,document.createElement('br'),button); return box;
  }));
}).catch(error => { message.textContent=error.message; });
