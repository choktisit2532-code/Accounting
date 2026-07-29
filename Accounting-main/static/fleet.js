const headers = {"Content-Type": "application/json"};
let vehicles = [], accounts = [], expenses = [], documents = [];
const money = v => new Intl.NumberFormat("th-TH", {style:"currency",currency:"THB"}).format(Number(v||0));
const safe = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const vehicleName = id => vehicles.find(v=>v.id===id)?.name || "รถ";
const toast = (text, bad=false) => { const x=document.querySelector("#toast"); x.textContent=text; x.className=bad?"show bad":"show"; setTimeout(()=>x.className="",2800); };
const showApp = () => {
  document.querySelector("#liff-loading").classList.add("hidden");
  document.querySelector("#liff-error").classList.add("hidden");
  document.querySelector("#fleet-app").classList.remove("hidden");
};
const showGateError = text => {
  document.querySelector("#liff-loading").classList.add("hidden");
  document.querySelector("#fleet-app").classList.add("hidden");
  document.querySelector("#liff-error-text").textContent=text;
  document.querySelector("#liff-error").classList.remove("hidden");
};
async function existingSession() {
  const res=await fetch("/api/auth/me",{credentials:"same-origin",headers:{Accept:"application/json"}});
  if (!res.ok) return false;
  setUser(await res.json());
  return true;
}
async function startSession() {
  if (await existingSession()) return true;
  const cfg=await fetch("/api/public/line-config").then(r=>r.json()).catch(()=>({}));
  if (!cfg.liff_id || !window.liff) return false;
  await liff.init({liffId:cfg.liff_id});
  if (!liff.isLoggedIn()) {
    liff.login({redirectUri:location.href});
    return null;
  }
  const accessToken=liff.getAccessToken();
  if (!accessToken) throw Error("ไม่พบสิทธิ์เข้าถึงบัญชี LINE");
  const res=await fetch("/api/auth/liff-session",{
    method:"POST",credentials:"same-origin",headers,
    body:JSON.stringify({access_token:accessToken})
  });
  const body=await res.json().catch(()=>({}));
  if (!res.ok) throw Error(body.detail||"ยืนยันบัญชี LINE ไม่สำเร็จ");
  setUser(body.user);
  return true;
}
async function api(path, options={}) {
  const res = await fetch(path, {...options, credentials:"same-origin", headers:{...headers,...(options.headers||{})}});
  if (res.status===401) { clearUser(); location.href="/login"; throw Error("unauthorized"); }
  const body = await res.json().catch(()=>({}));
  if (!res.ok) throw Error(body.detail||"ดำเนินการไม่สำเร็จ");
  return body;
}
async function load() {
  const [summary, vehicleRows, accountRows, expenseRows, documentRows] = await Promise.all([
    api("/api/fleet/dashboard"), api("/api/fleet/vehicles"), api("/api/accounts"),
    api("/api/fleet/expenses"), api("/api/fleet/documents")
  ]);
  vehicles=vehicleRows; accounts=accountRows; expenses=expenseRows; documents=documentRows;
  document.querySelector("#vehicle-count").textContent=summary.vehicles;
  document.querySelector("#month-expense").textContent=money(summary.monthly_expense);
  document.querySelector("#document-due").textContent=summary.documents_due;
  document.querySelector("#vehicle-list").innerHTML=vehicles.length ? vehicles.map(v=>`
    <div class="vehicle"><div class="car">🚙</div><div><strong>${safe(v.name)}</strong><span>${safe(v.plate_number)}</span></div><b>${Number(v.current_mileage).toLocaleString("th-TH")} กม.</b></div>`).join("") : `<p class="empty">ยังไม่มีรถ กด “เพิ่มรถ” เพื่อเริ่มใช้งาน</p>`;
  document.querySelector("#expense-list").innerHTML=expenses.length ? expenses.map(e=>`
    <div class="expense"><div><strong>${safe(e.category)}</strong><span>${safe(vehicleName(e.vehicle_id))} · ${safe(e.expense_date)}${e.garage_name?` · ${safe(e.garage_name)}`:""}</span></div><b>${money(e.amount)}</b>
    <div class="row-actions"><button data-edit-expense="${e.id}">แก้ไข</button><button class="danger" data-delete-expense="${e.id}">ลบ</button></div></div>`).join("") : `<p class="empty">ยังไม่มีค่าใช้จ่ายรถ</p>`;
  const today = new Date(); today.setHours(0,0,0,0);
  document.querySelector("#document-list").innerHTML=documents.length ? documents.map(d=>{
    const expiry=new Date(`${d.expiry_date}T00:00:00`); const days=Math.ceil((expiry-today)/86400000);
    const status=days<0?`หมดอายุแล้ว ${Math.abs(days)} วัน`:days<=30?`เหลือ ${days} วัน`:`หมดอายุ ${safe(d.expiry_date)}`;
    return `<div class="document"><div><strong>${safe(d.document_type)} · ${safe(vehicleName(d.vehicle_id))}</strong><span class="${days<=30?"due":""}">${status}</span></div><div class="row-actions"><button class="danger" data-delete-document="${d.id}">ลบ</button></div></div>`;
  }).join("") : `<p class="empty">ยังไม่มีเอกสารรถ</p>`;
  document.querySelectorAll(".vehicle-select").forEach(s=>s.innerHTML=vehicles.map(v=>`<option value="${v.id}">${safe(v.name)} · ${safe(v.plate_number)}</option>`).join(""));
  document.querySelectorAll(".account-select").forEach(s=>{ const first=s.querySelector("option")?.outerHTML||""; s.innerHTML=first+accounts.map(a=>`<option value="${a.id}">${safe(a.name)} (${money(a.balance)})</option>`).join(""); });
  document.querySelectorAll("[data-edit-expense]").forEach(b=>b.onclick=()=>editExpense(Number(b.dataset.editExpense)));
  document.querySelectorAll("[data-delete-expense]").forEach(b=>b.onclick=()=>removeExpense(Number(b.dataset.deleteExpense)));
  document.querySelectorAll("[data-delete-document]").forEach(b=>b.onclick=()=>removeDocument(Number(b.dataset.deleteDocument)));
}
document.querySelectorAll("[data-open]").forEach(b=>b.onclick=()=>{
  if (b.dataset.open!=="vehicle-dialog" && !vehicles.length) return toast("กรุณาเพิ่มรถก่อน",true);
  document.querySelector(`#${b.dataset.open}`).showModal();
});
document.querySelectorAll("[data-close]").forEach(b=>b.onclick=()=>b.closest("dialog").close());
document.querySelector("#logout").onclick=async()=>{await fetch("/api/auth/logout",{method:"POST",credentials:"same-origin"});clearUser();location.href="/login";};
document.querySelector("#expense-form [name=expense_date]").value=new Date().toISOString().slice(0,10);
document.querySelector("#document-form [name=expiry_date]").value=new Date().toISOString().slice(0,10);
function editExpense(id) {
  const item=expenses.find(e=>e.id===id); if (!item) return;
  const form=document.querySelector("#expense-form");
  for (const key of ["vehicle_id","category","amount","expense_date","garage_name","note"]) if (form.elements[key]) form.elements[key].value=item[key] ?? "";
  form.dataset.expenseId=id;
  form.querySelector("h2").textContent="แก้ไขค่าใช้จ่ายรถ";
  form.querySelector("button.primary").textContent="บันทึกการแก้ไข";
  document.querySelector("#expense-dialog").showModal();
}
async function removeExpense(id) {
  if (!confirm("ลบค่าใช้จ่ายนี้และคืนยอดกลับเข้าบัญชีใช่หรือไม่?")) return;
  try { await api(`/api/fleet/expenses/${id}`,{method:"DELETE"}); toast("ลบและคืนยอดบัญชีแล้ว"); await load(); }
  catch(err) { toast(err.message,true); }
}
async function removeDocument(id) {
  if (!confirm("ลบเอกสารรถรายการนี้ใช่หรือไม่?")) return;
  try { await api(`/api/fleet/documents/${id}`,{method:"DELETE"}); toast("ลบเอกสารแล้ว"); await load(); }
  catch(err) { toast(err.message,true); }
}
for (const [id,path] of [["vehicle-form","/api/fleet/vehicles"],["expense-form","/api/fleet/expenses"],["mileage-form","/api/fleet/mileages"],["document-form","/api/fleet/documents"]]) {
  document.querySelector(`#${id}`).onsubmit=async e=>{
    e.preventDefault(); const submit=e.target.querySelector("button.primary"); submit.disabled=true;
    const data=Object.fromEntries(new FormData(e.target)); const expenseId=e.target.dataset.expenseId;
    for (const key of ["vehicle_id","account_id","default_account_id","mileage"]) if (key in data) data[key]=data[key]?Number(data[key]):null;
    if (data.amount) data.amount=Number(data.amount);
    try {
      await api(expenseId?`${path}/${expenseId}`:path,{method:expenseId?"PUT":"POST",body:JSON.stringify(data)});
      e.target.closest("dialog").close(); e.target.reset(); delete e.target.dataset.expenseId;
      toast("บันทึกเรียบร้อยแล้ว"); await load();
      if (id==="expense-form") {
        e.target.querySelector("h2").textContent="บันทึกค่าใช้จ่ายรถ";
        e.target.querySelector("button.primary").textContent="บันทึกทั้ง Fleet และ Accounting";
        e.target.elements.expense_date.value=new Date().toISOString().slice(0,10);
      }
    } catch(err){toast(err.message,true);}
    finally { submit.disabled=false; }
  };
}
(async()=>{
  try {
    const ready=await startSession();
    if (ready===null) return;
    if (!ready) { location.replace("/login?next=/fleet"); return; }
    showApp();
    await load();
  } catch(err) {
    showGateError(err.message);
  }
})();
