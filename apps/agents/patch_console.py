"""Adds 'Add contact' to the console (apps/core/templates/core/console.html).
Run once from the project folder:  python patch_console.py
It checks every spot first; if anything does not match, it changes nothing."""
import shutil
import sys
from pathlib import Path

PATH = Path("apps/core/templates/core/console.html")

DIALOG = r"""<dialog id="cdlg" class="rounded-md border border-slate-200 p-5 shadow-lg backdrop:bg-slate-900/30">
  <div id="cd-title" class="text-sm font-medium">Add contact</div>
  <p class="mt-1 max-w-sm text-xs text-slate-500">Use an address you found yourself, such as the company’s contact page or a person’s profile. Do not guess addresses.</p>
  <label for="cd-name" class="mt-3 block text-xs text-slate-500">Name (optional)</label>
  <input id="cd-name" class="w-80 max-w-full rounded border border-slate-300 px-3 py-1.5 text-sm">
  <label for="cd-job" class="mt-3 block text-xs text-slate-500">Job title (optional)</label>
  <input id="cd-job" class="w-80 max-w-full rounded border border-slate-300 px-3 py-1.5 text-sm">
  <label for="cd-email" class="mt-3 block text-xs text-slate-500">Email</label>
  <input id="cd-email" type="email" class="w-80 max-w-full rounded border border-slate-300 px-3 py-1.5 text-sm">
  <div id="cd-err" class="mt-2 hidden text-xs text-red-700" role="alert"></div>
  <div class="mt-4 flex justify-end gap-2"><button type="button" class="btn" onclick="$('cdlg').close()">Cancel</button><button type="button" id="cd-save" class="btn-p" onclick="saveContact()">Save contact</button></div>
</dialog>

<script>
const API = '/api';"""

JS = r"""/* ---- Contacts ---- */
let contactLead = null;
const toHtml = l => email(l)
  ? `<span class="font-medium">${esc(email(l))}</span> <button class="text-xs text-teal-700 underline" onclick="openContact(${l.id})">Add another contact</button>`
  : `<span class="text-amber-800">No contact found, cannot send yet</span> <button class="btn" onclick="openContact(${l.id})">Add contact</button>`;
function openContact(id) {
  contactLead = id;
  ['cd-name', 'cd-job', 'cd-email'].forEach(k => $(k).value = '');
  $('cd-err').classList.add('hidden');
  $('cd-title').textContent = 'Add contact for ' + nameOfLead(id);
  $('cdlg').showModal();
  $('cd-email').focus();
}
async function saveContact() {
  const addr = $('cd-email').value.trim(), err = $('cd-err');
  if (!addr || !addr.includes('@')) { err.textContent = 'Enter a valid email address.'; err.classList.remove('hidden'); return; }
  $('cd-save').disabled = true;
  try {
    const l = await api(`/leads/${contactLead}/contacts/`, { method: 'POST', body: JSON.stringify({ email: addr, contact_name: $('cd-name').value.trim(), job_title: $('cd-job').value.trim() }) });
    S.leads = S.leads.map(x => x.id === l.id ? l : x);
    $('cdlg').close();
    say('Contact saved. Emails to this company now go to ' + (l.primary_email || addr) + '.');
    renderLeads();
    const t = $('toline'), it = cur();
    if (t && it && it.d.lead === l.id) t.innerHTML = '<span class="w-14 text-slate-500">To</span>' + toHtml(l);
  } catch (e) { err.textContent = e.message; err.classList.remove('hidden'); }
  finally { $('cd-save').disabled = false; }
}
function sentMsg(r) {
  if (r.sent === false) return { text: 'Approved but not sent.', bad: true };
  if (r.sent_status === 'sent' || r.sent === true) return 'Approved and sent.';
  if (r.sent_status === 'no_recipient') return { text: 'Approved, but there is no recipient yet. Add a contact, then press Approve and send again.', bad: true };
  if (r.sent_status === 'duplicate') return { text: 'Not sent: this company was already emailed from another campaign.', bad: true };
  return 'Approved. Sending.';
}

if (localStorage.getItem('token')) loadAll(); else openToken();"""

EDITS = [
    ("<script>\nconst API = '/api';", DIALOG),
    ("const email = l => (l.contacts && l.contacts.find(c => c.email) || {}).email;",
     "const email = l => l.primary_email || undefined;  // the address the backend will really use"),
    ("""<div class="flex flex-wrap items-center gap-2"><span class="w-14 text-slate-500">To</span>${email(lead) ? `<span class="font-medium">${esc(email(lead))}</span>` : '<span class="text-amber-800">No contact found, cannot send yet</span>'}</div>""",
     """<div id="toline" class="flex flex-wrap items-center gap-2"><span class="w-14 text-slate-500">To</span>${toHtml(lead)}</div>"""),
    ("""<td class="td">${email(l) ? esc(email(l)) : '<span class="text-amber-800">No contact found</span>'}</td>""",
     """<td class="td">${email(l) ? esc(email(l)) : '<span class="text-amber-800">No contact found</span> <button class="ml-1 text-xs text-teal-700 underline" onclick="openContact(' + l.id + ')">Add</button>'}</td>"""),
    ("try { const r = await fn(it); say(typeof okText === 'function' ? okText(r) : okText); S.sel = null; await loadAll(); }",
     "try { const r = await fn(it); const t = typeof okText === 'function' ? okText(r) : okText; say(t.text || t, t.bad); S.sel = null; await loadAll(); }"),
    ("r => r.sent === false ? 'Approved but not sent.' : 'Approved. Sending.');", "sentMsg);"),
    ("if (localStorage.getItem('token')) loadAll(); else openToken();", JS),
]


def main():
    if not PATH.exists():
        sys.exit(f"Cannot find {PATH}. Run this from the sales_agent folder.")
    text = PATH.read_text(encoding="utf-8")
    if "openContact" in text:
        sys.exit("Already patched. Nothing to do.")
    missing = [i + 1 for i, (old, _) in enumerate(EDITS) if text.count(old) != 1]
    if missing:
        sys.exit(f"Not patched. These spots in console.html were not found exactly once: {missing}. "
                 "The file may have been edited; send it to me and I will adapt the patch.")
    shutil.copy(PATH, PATH.with_suffix(".html.bak"))
    for old, new in EDITS:
        text = text.replace(old, new, 1)
    PATH.write_text(text, encoding="utf-8")
    print("Patched. A backup was saved as console.html.bak. Refresh the console (Ctrl+F5).")


main()