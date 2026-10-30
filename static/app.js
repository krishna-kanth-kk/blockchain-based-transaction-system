const $ = id => document.getElementById(id);
let currentUser = null;
let csrfToken = "";
const views = ["overview","pay","collect","invoices","customers","history","ledger","account"];
const savedTheme=localStorage.getItem("blockpay-theme")||"light";document.body.dataset.theme=savedTheme;
function setTheme(theme){document.body.dataset.theme=theme;localStorage.setItem("blockpay-theme",theme);const b=$("themeToggle");if(b)b.textContent=theme==="dark"?"Day mode":"Night mode"}
setTheme(savedTheme);
function toast(msg){const el=$("toast");if(!el)return;el.textContent=msg;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2600)}
async function api(url, options={}) {
  const headers={"Content-Type":"application/json",...(options.headers||{})};
  if(csrfToken && options.method && options.method!=='GET') headers["X-CSRF-Token"]=csrfToken;
  const res=await fetch(url,{...options,headers});
  const data=await res.json();
  if(!res.ok) throw new Error(data.error||"Request failed");
  return data;
}
function showView(view){
  views.forEach(v=>{
    const el=$(v==="overview"?"appView":v+"View");
    if(el) el.hidden=v!==view;
  });
  document.querySelectorAll(".nav").forEach(b=>b.classList.toggle("active",b.dataset.view===view));
  $("pageTitle").textContent=({overview:"Dashboard",pay:"Send money",collect:"Collect money",invoices:"Invoices",customers:"Customers",history:"Activity",ledger:"Audit trail",account:"Account settings"})[view];
  if(view==="history") loadHistory(); if(view==="ledger") loadLedger(); if(view==="collect") loadInvoiceCustomers(); if(view==="invoices") loadInvoices(); if(view==="customers") loadCustomers();
}
document.querySelectorAll(".nav").forEach(b=>b.addEventListener("click",()=>showView(b.dataset.view)));
document.querySelectorAll("[data-goto]").forEach(b=>b.addEventListener("click",()=>showView(b.dataset.goto)));
$("quickPay").onclick=()=>showView("pay");
async function loadInvoiceCustomers(){try{const list=await api('/api/customers');$('invoiceCustomer').innerHTML=list.length?list.map(c=>`<option value="${safe(c.customer_id)}">${safe(c.name)}${c.email?' · '+safe(c.email):''}</option>`).join(''):'<option value="">Add a customer first</option>';}catch(e){toast(e.message)}}
async function loadCustomers(){try{const list=await api('/api/customers');$('customerList').innerHTML=list.length?list.map(c=>`<div class="record"><div><strong>${safe(c.name)}</strong><small>${safe(c.email||c.phone||c.upi_id||'No contact details')}</small></div><div class="record-meta"><small>${safe(c.customer_id)}</small></div></div>`).join(''):'<div class="empty">No customers yet.</div>';}catch(e){$('customerList').textContent=e.message}}
async function loadInvoices(){try{const list=await api('/api/invoices');$('invoiceList').innerHTML=list.length?list.map(i=>`<div class="record"><div><strong>${safe(i.invoice_number)} · ${safe(i.customer_name)}</strong><small>${safe(i.description||'Payment request')} · Due ${safe(i.due_date||'—')}</small></div><div class="record-meta"><small class="invoice-status ${safe(i.status.toLowerCase())}">${safe(i.status)} · ${money(i.amount)}</small><button class="record-open" data-invoice="${safe(i.invoice_id)}" data-status="${safe(i.status)}">${i.status==='OPEN'?'Mark paid':'View'}</button></div></div>`).join(''):'<div class="empty">No invoices created yet.</div>'; document.querySelectorAll('[data-invoice]').forEach(b=>b.addEventListener('click',()=>markInvoice(b.dataset.invoice,b.dataset.status)));}catch(e){$('invoiceList').textContent=e.message}}
async function markInvoice(id,status){if(status!=='OPEN'){return}try{await api(`/api/invoices/${encodeURIComponent(id)}/status`,{method:'POST',body:JSON.stringify({status:'PAID'})});toast('Invoice marked paid');loadInvoices();refreshStats();}catch(e){toast(e.message)}}
$('newCustomerBtn')?.addEventListener('click',()=>{$('customerForm').hidden=!$('customerForm').hidden});
$('saveCustomerBtn')?.addEventListener('click',async()=>{try{await api('/api/customers',{method:'POST',body:JSON.stringify({name:$('customerName').value,email:$('customerEmail').value,phone:$('customerPhone').value,upi_id:$('customerUpi').value})});$('customerForm').hidden=true;['customerName','customerEmail','customerPhone','customerUpi'].forEach(id=>$(id).value='');toast('Customer added');loadCustomers();}catch(e){toast(e.message)}});
$('createInvoiceBtn')?.addEventListener('click',async()=>{try{const r=await api('/api/invoices',{method:'POST',body:JSON.stringify({customer_id:$('invoiceCustomer').value,amount:Number($('invoiceAmount').value),due_date:$('invoiceDue').value,description:$('invoiceDescription').value})});$('invoiceMessage').style.color='var(--lime)';$('invoiceMessage').textContent=`${r.invoice_number} created · ${r.payment_url}`;toast('Payment request created');}catch(e){$('invoiceMessage').textContent=e.message}});
async function loadInvoices(){
 try{
  const list=await api('/api/invoices');
  $('invoiceList').innerHTML=list.length?list.map(i=>`<div class="record"><div><strong>${safe(i.invoice_number)} · ${safe(i.customer_name)}</strong><small>${safe(i.description||'Payment request')} · Due ${safe(i.due_date||'—')}</small></div><div class="record-meta"><small class="invoice-status ${safe(i.status.toLowerCase())}">${safe(i.status)} · ${money(i.amount)}</small><a class="record-open" href="${safe(i.payment_url)}" target="_blank" rel="noopener">Open link</a>${i.status==='OPEN'?`<button class="record-open" data-invoice="${safe(i.invoice_id)}" data-status="${safe(i.status)}">Mark paid</button>`:''}</div></div>`).join(''):'<div class="empty">No invoices created yet.</div>';
  document.querySelectorAll('[data-invoice]').forEach(b=>b.addEventListener('click',()=>markInvoice(b.dataset.invoice,b.dataset.status)));
 }catch(e){$('invoiceList').textContent=e.message}
}
async function loadWallet(){
 try{const w=await api("/api/wallet"); const state=$("walletState"); if(!state)return; state.textContent=w.connected?`${w.address.slice(0,8)}…${w.address.slice(-6)} · ${w.network} · verified`:'No wallet connected'; state.className='wallet-state '+(w.connected?'connected':''); $("connectWalletBtn").hidden=!!w.connected; $("disconnectWalletBtn").hidden=!w.connected;}
 catch(e){console.error(e)}
}
async function connectWallet(){
 if(!window.ethereum){try{await api('/api/wallet/demo',{method:'POST',body:JSON.stringify({})});toast('Demo wallet connected');await loadWallet();}catch(e){toast(e.message)}return;}
 try{const accounts=await window.ethereum.request({method:'eth_requestAccounts'}); const chainHex=await window.ethereum.request({method:'eth_chainId'}); const address=accounts[0], chainId=parseInt(chainHex,16); const challenge=await api('/api/wallet/challenge',{method:'POST',body:JSON.stringify({address,chain_id:chainId})}); const signature=await window.ethereum.request({method:'personal_sign',params:[challenge.message,address]}); await api('/api/wallet/connect',{method:'POST',body:JSON.stringify({address,chain_id:chainId,network:`EVM ${chainId}`,signature})}); toast('Wallet verified and connected'); await loadWallet();}
 catch(e){toast(e.message)}
}
$("connectWalletBtn")?.addEventListener('click',connectWallet);
$("disconnectWalletBtn")?.addEventListener('click',async()=>{try{await api('/api/wallet',{method:'DELETE'});toast('Wallet disconnected');await loadWallet();}catch(e){toast(e.message)}});
$("themeToggle")?.addEventListener('click',()=>setTheme(document.body.dataset.theme==='dark'?'light':'dark'));
$("avatar")?.addEventListener('click',()=>{if(!currentUser)return;$("accountName").textContent=currentUser.name;$("accountUsername").textContent=currentUser.username;$("accountUpi").textContent=currentUser.upi;$("accountBalance").textContent=money(currentUser.balance);showView('account')});
$("accountBack")?.addEventListener('click',()=>showView('overview'));
$("loginBtn").onclick=async()=>{
  $("loginError").textContent="";
  try{await api("/api/login",{method:"POST",body:JSON.stringify({username:$("username").value,pin:$("pin").value})});await boot();}
  catch(e){$("loginError").textContent=e.message;}
};
$("logoutBtn").onclick=async()=>{await api("/api/logout",{method:"POST"});currentUser=null;location.reload();};
$("sendBtn").onclick=async()=>{
  $("payMessage").textContent="";
  try{
    const amount=Number($("amount").value);
    if(!amount||amount<=0) throw new Error("Enter an amount greater than zero");
    const result=await api("/api/pay",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({receiver:$("receiver").value,amount,note:$("note").value})});
    $("payMessage").style.color="var(--lime)";
    $("payMessage").textContent=`Payment recorded in block #${result.block.index}. ID: ${result.block.transaction.transaction_id}`;
    $("amount").value="";$("note").value="";toast("Signed payment added to the ledger");await refresh();
  }catch(e){$("payMessage").style.color="var(--red)";$("payMessage").textContent=e.message;}
};
$("verifyBtn")?.addEventListener("click",verifyLedger);$("ledgerVerify")?.addEventListener("click",verifyLedger);
$("tamperBtn")?.addEventListener("click",async()=>{try{const r=await api("/api/demo/tamper",{method:"POST"});toast(r.message);await refresh();await verifyLedger();}catch(e){toast(e.message)}});
$("resetBtn")?.addEventListener("click",async()=>{if(!confirm("Reset all demo transactions and restore balances?"))return;try{const r=await api("/api/demo/reset",{method:"POST"});toast(r.message);await refresh();await verifyLedger();}catch(e){toast(e.message)}});
async function verifyLedger(){
  try{const r=await api("/api/verify");const message=r.valid?"✓ "+r.message:"⚠ "+r.message;["verifyMessage","ledgerVerifyMessage"].forEach(id=>{if($(id)){$(id).textContent=message;$(id).style.color=r.valid?"var(--lime)":"var(--red)"}});toast(r.valid?"Ledger verified":"Ledger verification failed");await refreshStats();}
  catch(e){toast(e.message)}
}
function money(n){return "₹"+Number(n).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2})}
function safe(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
async function refreshStats(){
  const [s,a]=await Promise.all([api("/api/session"),api("/api/analytics")]);
  $("balance").textContent=money(s.balance);$("blockCount").textContent=a.transactions;$("volume").textContent=money(a.total_volume);if($("outstanding"))$("outstanding").textContent=money(a.outstanding_invoices||0);if($("invoiceCount"))$("invoiceCount").textContent=`${a.invoice_count||0} open requests`;
  $("integrity").textContent=a.verified?"Verified":"Compromised";$("integrity").className=a.verified?"verified":"negative";
  $("integritySub").textContent=a.verified?"Hash + signature checks":"Tampering detected";
}
function renderRecords(items,target){
  if(!items.length){$(target).innerHTML='<div class="empty">No payment records found.</div>';return;}
  $(target).innerHTML=items.map(x=>{
    const t=x.transaction, incoming=t.receiver===currentUser.upi;
    return `<div class="record"><div><strong>${incoming?"From":"To"} ${safe(incoming?t.sender:t.receiver)}</strong><small>${safe(t.transaction_id)} · ${safe(new Date(x.timestamp).toLocaleString())}</small></div><div class="record-meta"><small>${safe(t.note||"UPI transfer")} · Block #${x.index}</small><button class="record-open" data-tx="${safe(t.transaction_id)}">View receipt</button></div><div class="amount ${incoming?"positive":"negative"}">${incoming?"+":"−"}${money(t.amount)}</div></div>`;
  }).join("");
  document.querySelectorAll('.record-open').forEach(b=>b.addEventListener('click',()=>showReceipt(b.dataset.tx)));
}
async function showReceipt(id){try{const r=await api(`/api/transactions/${encodeURIComponent(id)}`);toast(`${r.transaction.status} · Block #${r.block.index} · ${r.block.hash.slice(0,12)}…`);}catch(e){toast(e.message)}}
let historyRecords=[];
function filteredHistory(){
 const q=($("historySearch")?.value||"").toLowerCase().trim(), filter=$("historyFilter")?.value||"all";
 return historyRecords.filter(x=>{const t=x.transaction, incoming=t.receiver===currentUser.upi; const hay=[t.transaction_id,t.sender,t.receiver,t.note].join(" ").toLowerCase(); return (!q||hay.includes(q))&&(filter==='all'||(filter==='received'&&incoming)||(filter==='sent'&&!incoming));});
}
async function loadHistory(){try{historyRecords=await api("/api/transactions");renderRecords(filteredHistory(),"historyList");}catch(e){$("historyList").textContent=e.message}}
function exportHistory(){
 const rows=filteredHistory().map(x=>{const t=x.transaction;return [x.timestamp,x.index,t.transaction_id,t.sender,t.receiver,t.amount,t.currency,t.status,t.note||""];});
 if(!rows.length){toast("No transactions to export");return;}
 const csv=[["timestamp","block","transaction_id","sender","receiver","amount","currency","status","note"],...rows].map(r=>r.map(v=>`"${String(v).replaceAll('"','""')}"`).join(",")).join("\n");
 const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));a.download="upi-chain-transactions.csv";a.click();URL.revokeObjectURL(a.href);
}
$("historySearch")?.addEventListener("input",()=>renderRecords(filteredHistory(),"historyList"));
$("historyFilter")?.addEventListener("change",()=>renderRecords(filteredHistory(),"historyList"));
$("exportBtn")?.addEventListener("click",exportHistory);
async function loadLedger(){
 try{const blocks=await api("/api/chain");if(!blocks.length){$("ledgerList").innerHTML='<div class="empty">The chain is empty. Make a payment to mine the first block.</div>';return;}
 $("ledgerList").innerHTML=blocks.slice().reverse().map(b=>`<div class="block-card"><div class="block-top"><b>BLOCK #${b.block_index}</b><span>${safe(new Date(b.timestamp).toLocaleString())}</span></div><p>${safe(b.transaction.sender)} → ${safe(b.transaction.receiver)} · ${money(b.transaction.amount)}</p><div class="hash">HASH&nbsp; ${safe(b.block_hash)}<br>PREVIOUS&nbsp; ${safe(b.previous_hash)}</div><small>Validators: ${safe(b.validators.join(" · "))}</small></div>`).join("");
 }catch(e){$("ledgerList").textContent=e.message}
}
async function refresh(){
 try{await refreshStats();const records=await api("/api/transactions");$("recentList").innerHTML=records.length?records.slice(0,4).map(x=>{const t=x.transaction,inc=t.receiver===currentUser.upi;return `<div class="record"><div><strong>${inc?"Received from":"Sent to"} ${safe(inc?t.sender:t.receiver)}</strong><small>${safe(t.transaction_id)} · Block #${x.index}</small></div><div></div><div class="amount ${inc?"positive":"negative"}">${inc?"+":"−"}${money(t.amount)}</div></div>`}).join(""):'<div class="empty">No transactions yet. Make a demo payment to create your first block.</div>';
 if(!$("historyView").hidden)loadHistory();if(!$("ledgerView").hidden)loadLedger();
 }catch(e){console.error(e)}
}
async function boot(){
 const csrf=await api("/api/csrf");csrfToken=csrf.token;
 const s=await api("/api/session");
 if(!s.logged_in){$("loginView").hidden=false;$("appView").hidden=true;return;}
 currentUser=s;$("loginView").hidden=true;$("appView").hidden=false;$("logoutBtn").hidden=false;
 $("userName").textContent=s.name;$("avatar").textContent=s.name[0];
 const users=await api("/api/users");$("receiver").innerHTML=users.map(u=>`<option value="${safe(u.username)}">${safe(u.name)} — ${safe(u.upi)}</option>`).join("");
 showView("overview");await refresh();
 await loadWallet();
}
boot().catch(()=>{});
setInterval(()=>{if(currentUser)refresh();},5000);
