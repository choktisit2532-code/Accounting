let trendChart = null;
let categoryChart = null;
let receiptObjectUrl = null;
let pairingCodeTimer = null;
let categoriesList = [];
let accountsList = [];
let savingsGoalsList = [];
let dashboardTransactions = [];
let historyTransactions = [];
let editingTransactionId = null;
let historyRequestId = 0;
let historySearchTimer = null;
let cashflowView = "day";
let dashboardType = "expense";
let latestDashboardData = null;

const byId = id => document.getElementById(id);
const money = amount => new Intl.NumberFormat("th-TH", {style: "currency", currency: "THB"}).format(Number(amount || 0));
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));
const safeColor = value => /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#6B7280";
const safeIcon = value => /^[a-z0-9 _-]{1,50}$/i.test(value || "") ? value : "fa-tag";
const BANGKOK_TIME_ZONE = "Asia/Bangkok";
function bangkokParts(value = new Date()) {
    return Object.fromEntries(new Intl.DateTimeFormat("en-US", {
        timeZone: BANGKOK_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", hourCycle: "h23"
    }).formatToParts(value).filter(part => part.type !== "literal").map(part => [part.type, part.value]));
}
const isoToday = () => {
    const {year, month, day} = bangkokParts();
    return `${year}-${month}-${day}`;
};
const compactMoney = amount => `฿${new Intl.NumberFormat("th-TH", {maximumFractionDigits: 0}).format(Number(amount || 0))}`;

function toast(message, kind = "success") {
    const element = byId("toast");
    element.textContent = message;
    element.className = `toast ${kind}`;
    clearTimeout(element._timer);
    element._timer = setTimeout(() => element.classList.add("hidden"), 3200);
}

function openModal(id) {
    const modal = byId(id);
    if (!modal) return;
    modal.classList.add("active");
    const focusable = modal.querySelector("input, select, button");
    if (focusable) setTimeout(() => focusable.focus(), 50);
}

function closeModal(id) {
    const modal = byId(id);
    if (modal) modal.classList.remove("active");
    if (id === "tx-modal") resetTransactionForm();
}

function selectedPeriod() {
    const value = byId("dashboard-period").value || isoToday().slice(0, 7);
    const [year, month] = value.split("-").map(Number);
    return {year, month};
}

function shiftSelectedPeriod(delta) {
    const {year, month} = selectedPeriod();
    const shifted = new Date(Date.UTC(year, month - 1 + delta, 1, 12));
    byId("dashboard-period").value = `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, "0")}`;
    byId("dashboard-period").dispatchEvent(new Event("change"));
}

function thaiMonthYear(year, month) {
    return new Intl.DateTimeFormat("th-TH", {timeZone: BANGKOK_TIME_ZONE, month: "long", year: "numeric"})
        .format(new Date(Date.UTC(year, month - 1, 1, 12)));
}

function formatThaiDate(date) {
    return new Intl.DateTimeFormat("th-TH", {
        dateStyle: "full", timeZone: BANGKOK_TIME_ZONE
    }).format(date);
}

async function requireOk(response, fallback) {
    if (response && response.ok) return response;
    throw new Error(await apiError(response, fallback));
}

document.addEventListener("DOMContentLoaded", async () => {
    if (!(await checkAuth())) return;
    const user = getUser();
    byId("username-display").textContent = user?.full_name || "ผู้ใช้";
    const hour = Number(bangkokParts().hour);
    const greeting = hour < 12 ? "สวัสดีตอนเช้า" : hour < 17 ? "สวัสดีตอนบ่าย" : "สวัสดีตอนเย็น";
    byId("greeting-text").textContent = `${greeting}, ${user?.full_name || ""}`;
    byId("current-date").textContent = formatThaiDate(new Date());
    byId("dashboard-period").value = isoToday().slice(0, 7);
    byId("tx-date").value = isoToday();
    const {year: currentYear, month: currentMonth, day: currentDay} = bangkokParts();
    const nextMonthDate = new Date(Date.UTC(Number(currentYear), Number(currentMonth), Number(currentDay), 12));
    const nextMonthParts = bangkokParts(nextMonthDate);
    byId("savings-date").value = `${nextMonthParts.year}-${nextMonthParts.month}-${nextMonthParts.day}`;
    populateMonthOptions();
    syncBudgetPeriod();
    bindEvents();
    await loadAllData();
});

function bindEvents() {
    document.querySelectorAll("[data-open-modal]").forEach(button => {
        button.addEventListener("click", () => openModal(button.dataset.openModal));
    });
    document.querySelectorAll("[data-close-modal]").forEach(button => {
        button.addEventListener("click", () => closeModal(button.dataset.closeModal));
    });
    document.querySelectorAll(".modal-overlay").forEach(modal => {
        modal.addEventListener("click", event => {
            if (event.target === modal) closeModal(modal.id);
        });
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            const active = document.querySelector(".modal-overlay.active");
            if (active) closeModal(active.id);
        }
    });
    byId("logout-btn").addEventListener("click", logout);
    byId("tx-type").addEventListener("change", handleTxTypeChange);
    byId("dashboard-period").addEventListener("change", async () => {
        syncBudgetPeriod();
        await Promise.all([fetchDashboardData(), fetchBudgets()]);
    });
    byId("period-prev").addEventListener("click", () => shiftSelectedPeriod(-1));
    byId("period-next").addEventListener("click", () => shiftSelectedPeriod(1));
    document.querySelectorAll("[data-dashboard-type]").forEach(button => {
        button.addEventListener("click", () => {
            dashboardType = button.dataset.dashboardType;
            document.querySelectorAll("[data-dashboard-type]").forEach(item => {
                const active = item.dataset.dashboardType === dashboardType;
                item.classList.toggle("active", active);
                item.setAttribute("aria-selected", String(active));
            });
            renderDashboardFocus();
        });
    });
    document.querySelectorAll("[data-cashflow-view]").forEach(button => {
        button.addEventListener("click", () => {
            cashflowView = button.dataset.cashflowView;
            document.querySelectorAll("[data-cashflow-view]").forEach(item => {
                const active = item.dataset.cashflowView === cashflowView;
                item.classList.toggle("active", active);
                item.setAttribute("aria-pressed", String(active));
            });
            if (latestDashboardData) {
                const {month, year} = selectedPeriod();
                renderCashflowChart(latestDashboardData, year, month);
            }
        });
    });
    byId("tx-form").addEventListener("submit", submitTransaction);
    byId("wallet-form").addEventListener("submit", submitWallet);
    byId("budget-form").addEventListener("submit", submitBudget);
    byId("savings-form").addEventListener("submit", submitSavingsGoal);
    byId("category-form").addEventListener("submit", submitCategory);
    byId("category-manager-btn").addEventListener("click", () => {
        renderCategoryManager();
        openModal("category-modal");
    });
    byId("history-filter-form").addEventListener("submit", event => {
        event.preventDefault();
        fetchHistory();
    });
    ["history-type", "history-start", "history-end"].forEach(id => {
        byId(id).addEventListener("change", fetchHistory);
    });
    byId("history-search").addEventListener("input", () => {
        clearTimeout(historySearchTimer);
        historySearchTimer = setTimeout(fetchHistory, 350);
    });
    byId("export-pdf-btn").addEventListener("click", exportPdf);
    byId("get-pairing-code-btn").addEventListener("click", fetchPairingCode);
    byId("unlink-line-btn").addEventListener("click", unlinkLine);
    byId("wallets-list").addEventListener("click", walletAction);
    byId("transactions-list").addEventListener("click", transactionAction);
    byId("history-list").addEventListener("click", transactionAction);
    byId("budgets-list").addEventListener("click", budgetAction);
    byId("savings-list").addEventListener("click", savingsAction);
    byId("category-manager-list").addEventListener("click", categoryAction);
    document.querySelectorAll(".primary-nav a").forEach(link => {
        link.addEventListener("click", () => {
            document.querySelectorAll(".primary-nav a").forEach(item => item.classList.remove("active"));
            link.classList.add("active");
        });
    });
}

async function logout() {
    await authenticatedFetch("/api/auth/logout", {method: "POST"});
    clearUser();
    window.location.replace("/login");
}

function populateMonthOptions() {
    const names = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];
    byId("budget-month").innerHTML = names.map((name, index) => `<option value="${index + 1}">${name}</option>`).join("");
}

function syncBudgetPeriod() {
    const period = selectedPeriod();
    byId("budget-month").value = period.month;
    byId("budget-year").value = period.year;
}

async function loadAllData() {
    try {
        await Promise.all([fetchCategories(), fetchAccounts()]);
        await Promise.all([fetchDashboardData(), fetchBudgets(), fetchSavingsGoals(), fetchHistory(), fetchLineStatus()]);
    } catch (error) {
        toast(error.message || "โหลดข้อมูลไม่สำเร็จ", "error");
    }
}

async function fetchCategories() {
    const response = await requireOk(await authenticatedFetch("/api/categories"), "โหลดหมวดหมู่ไม่สำเร็จ");
    categoriesList = await response.json();
    populateCategoryOptions(byId("tx-type").value);
    byId("budget-category_id").innerHTML = categoriesList
        .filter(item => item.type === "expense")
        .map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
    renderCategoryManager();
}

function populateCategoryOptions(type) {
    byId("tx-category_id").innerHTML = categoriesList
        .filter(item => item.type === type)
        .map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
}

async function fetchAccounts() {
    const response = await requireOk(await authenticatedFetch("/api/accounts"), "โหลดบัญชีไม่สำเร็จ");
    accountsList = await response.json();
    renderWallets();
    const options = accountsList.map(item => `<option value="${item.id}">${escapeHtml(item.name)} (${money(item.balance)})</option>`).join("");
    byId("tx-account_id").innerHTML = options;
    byId("tx-to_account_id").innerHTML = options;
}

function renderWallets() {
    const icons = {cash: "fa-wallet", bank: "fa-building-columns", credit_card: "fa-credit-card", investment: "fa-chart-line", other: "fa-vault"};
    const cards = accountsList.map(account => `
        <article class="wallet-card ${escapeHtml(account.type)}" data-account-id="${account.id}">
            <div class="wallet-top"><span class="wallet-logo"><i class="fa-solid ${icons[account.type] || icons.other}"></i></span><span class="wallet-type">${escapeHtml(account.type)}</span></div>
            <div class="wallet-balance-container"><div class="wallet-balance-label">ยอดเงินคงเหลือ</div><div class="wallet-balance">${money(account.balance)}</div></div>
            <div class="wallet-bottom"><div class="wallet-name">${escapeHtml(account.name)}</div>
                <div class="wallet-actions">
                    <button class="btn-icon" data-wallet-action="reconcile" title="ตรวจสอบและปรับยอด"><i class="fa-solid fa-scale-balanced"></i></button>
                    <button class="btn-icon delete-btn" data-wallet-action="delete" title="ลบบัญชี"><i class="fa-solid fa-trash-can"></i></button>
                </div>
            </div>
        </article>`).join("");
    const add = `<button class="add-wallet-card" type="button" data-wallet-action="add"><i class="fa-solid fa-circle-plus"></i><span>สร้างบัญชีใหม่</span></button>`;
    byId("wallets-list").innerHTML = cards + add;
}

async function walletAction(event) {
    const button = event.target.closest("[data-wallet-action]");
    if (!button) return;
    const action = button.dataset.walletAction;
    if (action === "add") return openModal("wallet-modal");
    const card = button.closest("[data-account-id]");
    const account = accountsList.find(item => item.id === Number(card?.dataset.accountId));
    if (!account) return;
    if (action === "reconcile") await reconcileAccount(account);
    if (action === "delete") await deleteAccount(account);
}

async function reconcileAccount(account) {
    const raw = prompt(`ยอดจริงของบัญชี “${account.name}” เท่ากับเท่าไร?`, account.balance);
    if (raw === null || raw.trim() === "" || !Number.isFinite(Number(raw))) return;
    try {
        await requireOk(await authenticatedFetch(`/api/accounts/${account.id}/reconcile`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({actual_balance: raw, note: "ปรับยอดจากหน้าแดชบอร์ด"})
        }), "ปรับยอดไม่สำเร็จ");
        toast("ปรับยอดพร้อมสร้างประวัติรายการแล้ว");
        await loadAllData();
    } catch (error) { toast(error.message, "error"); }
}

async function deleteAccount(account) {
    if (!confirm(`ลบบัญชี “${account.name}” หรือไม่? ระบบจะไม่อนุญาตหากมีประวัติธุรกรรม`)) return;
    try {
        await requireOk(await authenticatedFetch(`/api/accounts/${account.id}`, {method: "DELETE"}), "ลบบัญชีไม่สำเร็จ");
        toast("ลบบัญชีแล้ว");
        await fetchAccounts();
    } catch (error) { toast(error.message, "error"); }
}

async function fetchDashboardData() {
    const {month, year} = selectedPeriod();
    const response = await requireOk(await authenticatedFetch(`/api/reports/dashboard?month=${month}&year=${year}`), "โหลดแดชบอร์ดไม่สำเร็จ");
    const data = await response.json();
    latestDashboardData = data;
    byId("total-balance").textContent = money(data.net_worth);
    byId("month-income").textContent = money(data.month_income);
    byId("month-expense").textContent = money(data.month_expense);
    byId("month-cashflow").textContent = money(data.cash_flow);
    byId("month-cashflow").className = `amount ${data.cash_flow >= 0 ? "income-text" : "expense-text"}`;
    byId("savings-rate").textContent = `อัตราออม ${Number(data.savings_rate).toFixed(1)}%`;
    const periodLabel = thaiMonthYear(year, month);
    byId("summary-period-display").textContent = periodLabel;
    byId("income-period-label").textContent = "รายรับ";
    byId("expense-period-label").textContent = "รายจ่าย";
    dashboardTransactions = data.monthly_transactions || data.recent_transactions;
    renderDashboardFocus();
    renderCashflowChart(data, year, month);
}

function renderDashboardFocus() {
    if (!latestDashboardData) return;
    const labels = {expense: "รายจ่าย", income: "รายรับ", transfer: "โอนเงิน"};
    const label = labels[dashboardType];
    const {month, year} = selectedPeriod();
    const periodLabel = thaiMonthYear(year, month);
    const totals = {
        expense: latestDashboardData.month_expense,
        income: latestDashboardData.month_income,
        transfer: latestDashboardData.month_transfer || 0
    };
    const items = dashboardTransactions.filter(item => item.type === dashboardType);
    let breakdown = latestDashboardData.category_breakdowns?.[dashboardType] || [];
    if (dashboardType === "transfer" && Number(totals.transfer) > 0) {
        breakdown = [{category_name: "โอนระหว่างบัญชี", color: "#3157F6", icon: "fa-right-left", amount: totals.transfer}];
    }

    byId("recent-period-title").textContent = `${label} · ${periodLabel}`;
    byId("recent-total-label").textContent = `${label}รวมเดือนนี้`;
    byId("recent-expense-total").textContent = money(totals[dashboardType]);
    byId("recent-expense-total").className = `${dashboardType}-text`;
    byId("category-panel-title").textContent = dashboardType === "transfer" ? "ยอดโอนระหว่างบัญชี" : `สัดส่วน${label}ตามหมวด`;
    byId("category-total-label").textContent = `รวม${label}`;
    byId("category-empty").textContent = `ยังไม่มี${label}ใน${periodLabel}`;
    renderTransactionList(items, byId("transactions-list"));
    renderCategoryChart(breakdown);
    renderCategoryBreakdownList(breakdown, totals[dashboardType]);
}

function renderCategoryBreakdownList(data, total) {
    const container = byId("category-breakdown-list");
    const totalValue = Number(total || 0);
    if (!data.length) {
        container.innerHTML = '<div class="category-list-empty"><i class="fa-solid fa-chart-pie"></i><span>เมื่อมีรายการ ระบบจะแสดงสัดส่วนตรงนี้</span></div>';
        return;
    }
    container.innerHTML = data.map((item, index) => {
        const percent = totalValue > 0 ? Number(item.amount) / totalValue * 100 : 0;
        return `<div class="category-rank-row">
            <span class="category-rank">${index + 1}</span>
            <span class="category-rank-icon" style="background:${safeColor(item.color)}"><i class="fa-solid ${safeIcon(item.icon)}"></i></span>
            <div class="category-rank-copy"><strong>${escapeHtml(item.category_name)}</strong><span>${percent.toFixed(1)}% ของยอดรวม</span></div>
            <strong class="category-rank-amount">${money(item.amount)}</strong>
        </div>`;
    }).join("");
}

function normalizedTransaction(item) {
    const category = categoriesList.find(cat => cat.id === item.category_id);
    const account = accountsList.find(acc => acc.id === item.account_id);
    const destination = accountsList.find(acc => acc.id === item.to_account_id);
    return {
        ...item,
        category_name: item.category_name || category?.name || (item.type === "transfer" ? "โอนเงิน" : "ไม่มีหมวดหมู่"),
        category_icon: item.category_icon || category?.icon || (item.type === "transfer" ? "fa-right-left" : "fa-tag"),
        category_color: item.category_color || category?.color || (item.type === "transfer" ? "#3B82F6" : "#6B7280"),
        account_name: item.account_name || account?.name || "-",
        to_account_name: item.to_account_name || destination?.name || null,
        has_receipt: item.has_receipt ?? Boolean(item.receipt_path)
    };
}

function renderTransactionList(items, container, compact = false) {
    if (!items.length) {
        container.innerHTML = `<div class="empty-state"><i class="fa-solid fa-receipt"></i><p>ยังไม่มีรายการ</p></div>`;
        return;
    }
    container.innerHTML = items.map(raw => {
        const item = normalizedTransaction(raw);
        const sign = item.type === "income" ? "+" : item.type === "expense" ? "-" : "";
        const accountText = item.type === "transfer" ? `${item.account_name} → ${item.to_account_name || "-"}` : item.account_name;
        return `<article class="transaction-item" data-tx-id="${item.id}">
            <div class="tx-left"><div class="tx-cat-icon" style="background-color:${safeColor(item.category_color)}"><i class="fa-solid ${safeIcon(item.category_icon)}"></i></div>
                <div class="tx-info"><h3>${escapeHtml(item.category_name)}</h3><p>${escapeHtml(accountText)} · ${new Date(item.date + "T12:00:00+07:00").toLocaleDateString("th-TH", {timeZone: BANGKOK_TIME_ZONE})}</p>${!compact && item.note ? `<p class="tx-note">${escapeHtml(item.note)}</p>` : ""}</div></div>
            <div class="tx-right"><div class="tx-amount ${escapeHtml(item.type)}">${sign}${money(item.amount)}</div>
                <div class="tx-actions">
                    ${item.has_receipt ? '<button class="btn-icon" data-tx-action="receipt" title="ดูใบเสร็จ"><i class="fa-solid fa-image"></i></button>' : ""}
                    <button class="btn-icon" data-tx-action="edit" title="แก้ไข"><i class="fa-solid fa-pen"></i></button>
                    <button class="btn-icon delete-btn" data-tx-action="delete" title="ลบ"><i class="fa-solid fa-trash-can"></i></button>
                </div>
            </div></article>`;
    }).join("");
}

async function transactionAction(event) {
    const button = event.target.closest("[data-tx-action]");
    if (!button) return;
    const id = Number(button.closest("[data-tx-id]")?.dataset.txId);
    if (button.dataset.txAction === "receipt") await viewReceipt(id);
    if (button.dataset.txAction === "edit") editTransaction(id);
    if (button.dataset.txAction === "delete") await deleteTransaction(id);
}

function findTransaction(id) {
    return dashboardTransactions.find(item => item.id === id) || historyTransactions.find(item => item.id === id);
}

function editTransaction(id) {
    const item = findTransaction(id);
    if (!item) return;
    editingTransactionId = id;
    byId("tx-modal-title").textContent = `แก้ไขธุรกรรม #${id}`;
    byId("tx-submit-btn").textContent = "บันทึกการแก้ไข";
    byId("receipt-group").classList.add("hidden");
    byId("tx-type").value = item.type;
    handleTxTypeChange();
    byId("tx-amount").value = item.amount;
    byId("tx-account_id").value = item.account_id;
    byId("tx-category_id").value = item.category_id || "";
    byId("tx-to_account_id").value = item.to_account_id || "";
    byId("tx-date").value = item.date;
    byId("tx-note").value = item.note || "";
    openModal("tx-modal");
}

function resetTransactionForm() {
    editingTransactionId = null;
    const form = byId("tx-form");
    if (!form) return;
    form.reset();
    byId("tx-modal-title").textContent = "บันทึกรายรับ-รายจ่าย";
    byId("tx-submit-btn").textContent = "บันทึกรายการ";
    byId("receipt-group").classList.remove("hidden");
    byId("tx-date").value = isoToday();
    byId("tx-type").value = "expense";
    handleTxTypeChange();
}

function handleTxTypeChange() {
    const type = byId("tx-type").value;
    const transfer = type === "transfer";
    byId("tx-category-group").classList.toggle("hidden", transfer);
    byId("tx-to-account-group").classList.toggle("hidden", !transfer);
    byId("tx-account-label").textContent = transfer ? "โอนจากบัญชี" : type === "income" ? "รับเข้าบัญชี" : "จ่ายจากบัญชี";
    if (!transfer) populateCategoryOptions(type);
}

async function submitTransaction(event) {
    event.preventDefault();
    const wasEditing = Boolean(editingTransactionId);
    const type = byId("tx-type").value;
    try {
        let response;
        if (editingTransactionId) {
            const payload = {
                type,
                amount: byId("tx-amount").value,
                account_id: Number(byId("tx-account_id").value),
                category_id: type === "transfer" ? null : (Number(byId("tx-category_id").value) || null),
                to_account_id: type === "transfer" ? (Number(byId("tx-to_account_id").value) || null) : null,
                date: byId("tx-date").value,
                note: byId("tx-note").value
            };
            response = await authenticatedFetch(`/api/transactions/${editingTransactionId}`, {
                method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
            });
        } else {
            const form = new FormData();
            form.append("type", type);
            form.append("amount", byId("tx-amount").value);
            form.append("account_id", byId("tx-account_id").value);
            form.append("date_val", byId("tx-date").value);
            if (byId("tx-note").value) form.append("note", byId("tx-note").value);
            if (type === "transfer") form.append("to_account_id", byId("tx-to_account_id").value);
            else if (byId("tx-category_id").value) form.append("category_id", byId("tx-category_id").value);
            if (byId("tx-receipt").files[0]) form.append("receipt", byId("tx-receipt").files[0]);
            response = await authenticatedFetch("/api/transactions", {method: "POST", body: form});
        }
        await requireOk(response, "บันทึกรายการไม่สำเร็จ");
        closeModal("tx-modal");
        toast(wasEditing ? "แก้ไขรายการแล้ว" : "บันทึกรายการแล้ว");
        await loadAllData();
    } catch (error) { toast(error.message, "error"); }
}

async function deleteTransaction(id) {
    if (!confirm("ลบรายการนี้หรือไม่? ระบบจะคืนยอดบัญชีให้อัตโนมัติ")) return;
    try {
        await requireOk(await authenticatedFetch(`/api/transactions/${id}`, {method: "DELETE"}), "ลบรายการไม่สำเร็จ");
        toast("ลบรายการและคืนยอดแล้ว");
        await loadAllData();
    } catch (error) { toast(error.message, "error"); }
}

async function viewReceipt(id) {
    try {
        const response = await requireOk(await authenticatedFetch(`/api/transactions/${id}/receipt`), "เปิดใบเสร็จไม่สำเร็จ");
        if (receiptObjectUrl) URL.revokeObjectURL(receiptObjectUrl);
        receiptObjectUrl = URL.createObjectURL(await response.blob());
        byId("receipt-img-view").src = receiptObjectUrl;
        openModal("receipt-modal");
    } catch (error) { toast(error.message, "error"); }
}

function renderCashflowChart(report, year, month) {
    if (trendChart) trendChart.destroy();
    const daily = cashflowView === "day";
    const periodLabel = thaiMonthYear(year, month);
    const yearLabel = `ปี ${year + 543}`;
    const monthNames = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
    const data = daily ? report.daily_cashflow : report.yearly_cashflow;
    const totalIncome = daily
        ? Number(report.month_income)
        : data.reduce((sum, item) => sum + Number(item.income || 0), 0);
    const totalExpense = daily
        ? Number(report.month_expense)
        : data.reduce((sum, item) => sum + Number(item.expense || 0), 0);
    const net = totalIncome - totalExpense;
    const totalPeriodLabel = daily ? "เดือนนี้" : `ปี ${year + 543}`;
    byId("daily-chart-title").textContent = daily
        ? `กระแสเงินรายวัน · ${periodLabel}`
        : `กระแสเงินรายเดือน · ${yearLabel}`;
    byId("daily-total-income-label").textContent = `รายรับ${totalPeriodLabel}`;
    byId("daily-total-expense-label").textContent = `รายจ่าย${totalPeriodLabel}`;
    byId("daily-total-net-label").textContent = `สุทธิ${totalPeriodLabel}`;
    byId("daily-total-income").textContent = money(totalIncome);
    byId("daily-total-expense").textContent = money(totalExpense);
    byId("daily-total-net").textContent = money(net);
    byId("daily-total-net").className = net >= 0 ? "income-text" : "expense-text";
    const options = chartOptions({currencyAxis: true, axisMode: daily ? "day" : "month"});
    options.plugins.tooltip.callbacks.title = items => {
        const item = data[items[0]?.dataIndex];
        if (!item) return daily ? periodLabel : yearLabel;
        return daily ? `วันที่ ${item.day} · ${periodLabel}` : `${monthNames[item.month - 1]} ${year + 543}`;
    };
    trendChart = new Chart(byId("trendChart"), {
        type: "bar",
        data: {labels: data.map(item => daily ? item.day : monthNames[item.month - 1]), datasets: [
            {label: "รายรับ", data: data.map(item => item.income), backgroundColor: "rgba(11,168,115,.78)", hoverBackgroundColor: "#0BA873", borderRadius: 7, borderSkipped: false},
            {label: "รายจ่าย", data: data.map(item => item.expense), backgroundColor: "rgba(241,91,93,.72)", hoverBackgroundColor: "#F15B5D", borderRadius: 7, borderSkipped: false}
        ]},
        plugins: [barValuePlugin],
        options
    });
}

function renderCategoryChart(data) {
    if (categoryChart) categoryChart.destroy();
    byId("category-empty").classList.toggle("hidden", data.length > 0);
    byId("category-total-box").classList.toggle("hidden", data.length === 0);
    byId("category-total").textContent = money(data.reduce((sum, item) => sum + Number(item.amount || 0), 0));
    if (!data.length) return;
    categoryChart = new Chart(byId("categoryChart"), {
        type: "doughnut",
        data: {labels: data.map(item => item.category_name), datasets: [{
            data: data.map(item => item.amount), backgroundColor: data.map(item => safeColor(item.color)), borderWidth: 0
        }]},
        options: {...chartOptions(), cutout: "65%"}
    });
}

const barValuePlugin = {
    id: "barValues",
    afterDatasetsDraw(chart) {
        const {ctx} = chart;
        ctx.save();
        ctx.fillStyle = "#344054";
        ctx.font = "600 10px Sarabun";
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        chart.data.datasets.forEach((dataset, datasetIndex) => {
            chart.getDatasetMeta(datasetIndex).data.forEach((bar, index) => {
                const value = Number(dataset.data[index] || 0);
                if (value > 0) ctx.fillText(compactMoney(value), bar.x, Math.max(bar.y - 5, 14));
            });
        });
        ctx.restore();
    }
};

function chartOptions({currencyAxis = false, axisMode = null} = {}) {
    return {
        responsive: true, maintainAspectRatio: false,
        layout: currencyAxis ? {padding: {top: 20}} : undefined,
        plugins: {
            legend: {labels: {color: "#53617B", usePointStyle: true, pointStyle: "rectRounded", font: {family: "Sarabun", size: 11}}},
            tooltip: {callbacks: {label: context => `${context.dataset.label || "ยอดรวม"}: ${money(context.raw)}`}}
        },
        scales: currencyAxis ? {
            y: {beginAtZero: true, ticks: {callback: value => compactMoney(value), color: "#7A859B"}, grid: {color: "rgba(130,143,169,.14)"}},
            x: {
                ticks: {color: "#7A859B", autoSkip: !axisMode, maxRotation: 0, font: {size: axisMode === "day" ? 9 : 11}},
                title: axisMode ? {display: true, text: axisMode === "day" ? "วันที่" : "เดือน", color: "#7A859B", font: {family: "Sarabun", size: 11}} : undefined,
                grid: {display: false}
            }
        } : undefined
    };
}

async function fetchBudgets() {
    const {month, year} = selectedPeriod();
    const response = await requireOk(await authenticatedFetch(`/api/budgets?month=${month}&year=${year}`), "โหลดงบประมาณไม่สำเร็จ");
    const budgets = await response.json();
    if (!budgets.length) {
        byId("budgets-list").innerHTML = '<p class="empty-state">ยังไม่ได้ตั้งงบประมาณสำหรับเดือนนี้</p>';
        return;
    }
    byId("budgets-list").innerHTML = budgets.map(item => {
        const percent = Math.min(item.spent_amount / item.limit_amount * 100, 100);
        const color = percent > 90 ? "#EF4444" : percent > 70 ? "#F59E0B" : safeColor(item.category_color);
        return `<div class="budget-progress-container" data-budget-id="${item.id}">
            <div class="budget-info-row"><strong><i class="fa-solid ${safeIcon(item.category_icon)}"></i> ${escapeHtml(item.category_name)}</strong><button class="btn-icon delete-btn" data-budget-action="delete"><i class="fa-solid fa-xmark"></i></button></div>
            <div class="budget-progress-bar"><div class="budget-progress-fill" style="width:${percent}%;background:${color}"></div></div>
            <div class="budget-spent-info"><span>ใช้แล้ว ${money(item.spent_amount)}</span><span>งบ ${money(item.limit_amount)}</span></div>
        </div>`;
    }).join("");
}

async function budgetAction(event) {
    const button = event.target.closest("[data-budget-action]");
    if (!button || !confirm("ลบงบประมาณนี้หรือไม่?")) return;
    const id = Number(button.closest("[data-budget-id]").dataset.budgetId);
    try {
        await requireOk(await authenticatedFetch(`/api/budgets/${id}`, {method: "DELETE"}), "ลบงบไม่สำเร็จ");
        await fetchBudgets();
    } catch (error) { toast(error.message, "error"); }
}

async function fetchSavingsGoals() {
    const response = await requireOk(await authenticatedFetch("/api/savings"), "โหลดเป้าหมายออมไม่สำเร็จ");
    savingsGoalsList = await response.json();
    if (!savingsGoalsList.length) {
        byId("savings-list").innerHTML = '<p class="empty-state">ยังไม่มีเป้าหมายการออม</p>';
        return;
    }
    byId("savings-list").innerHTML = savingsGoalsList.map(goal => {
        const percent = Math.min(Number(goal.current_amount) / Number(goal.target_amount) * 100, 100);
        return `<div class="goal-item" data-goal-id="${goal.id}">
            <div class="goal-header"><div><h3>${escapeHtml(goal.name)}</h3><p>เป้าหมาย ${new Date(goal.target_date + "T12:00:00+07:00").toLocaleDateString("th-TH", {timeZone: BANGKOK_TIME_ZONE})}</p></div><button class="btn-icon delete-btn" data-saving-action="delete"><i class="fa-solid fa-trash-can"></i></button></div>
            <div class="budget-progress-bar"><div class="budget-progress-fill savings-fill" style="width:${percent}%"></div></div>
            <div class="budget-spent-info"><span>สะสม ${money(goal.current_amount)} (${percent.toFixed(1)}%)</span><span>${money(goal.target_amount)}</span></div>
            <div class="goal-actions"><button class="btn btn-secondary compact-btn" data-saving-action="contribute"><i class="fa-solid fa-piggy-bank"></i> ออมเพิ่ม</button></div>
        </div>`;
    }).join("");
}

async function savingsAction(event) {
    const button = event.target.closest("[data-saving-action]");
    if (!button) return;
    const id = Number(button.closest("[data-goal-id]").dataset.goalId);
    if (button.dataset.savingAction === "delete") {
        if (!confirm("ลบเป้าหมายนี้หรือไม่?")) return;
        try {
            await requireOk(await authenticatedFetch(`/api/savings/${id}`, {method: "DELETE"}), "ลบเป้าหมายไม่สำเร็จ");
            await fetchSavingsGoals();
        } catch (error) { toast(error.message, "error"); }
    } else {
        const raw = prompt("ต้องการเพิ่มยอดสะสมกี่บาท?");
        if (!raw || Number(raw) <= 0) return;
        try {
            await requireOk(await authenticatedFetch(`/api/savings/${id}/contribute`, {
                method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({amount: raw})
            }), "เพิ่มยอดสะสมไม่สำเร็จ");
            await fetchSavingsGoals();
        } catch (error) { toast(error.message, "error"); }
    }
}

async function submitWallet(event) {
    event.preventDefault();
    try {
        await requireOk(await authenticatedFetch("/api/accounts", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name: byId("wallet-name").value, type: byId("wallet-type").value, balance: byId("wallet-balance").value})
        }), "สร้างบัญชีไม่สำเร็จ");
        closeModal("wallet-modal");
        byId("wallet-form").reset();
        toast("สร้างบัญชีแล้ว");
        await loadAllData();
    } catch (error) { toast(error.message, "error"); }
}

async function submitBudget(event) {
    event.preventDefault();
    try {
        await requireOk(await authenticatedFetch("/api/budgets", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                category_id: Number(byId("budget-category_id").value),
                limit_amount: byId("budget-limit").value,
                month: Number(byId("budget-month").value),
                year: Number(byId("budget-year").value)
            })
        }), "บันทึกงบประมาณไม่สำเร็จ");
        closeModal("budget-modal");
        byId("budget-limit").value = "";
        await fetchBudgets();
    } catch (error) { toast(error.message, "error"); }
}

async function submitSavingsGoal(event) {
    event.preventDefault();
    try {
        await requireOk(await authenticatedFetch("/api/savings", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name: byId("savings-name").value,
                target_amount: byId("savings-target").value,
                current_amount: byId("savings-current").value,
                target_date: byId("savings-date").value
            })
        }), "สร้างเป้าหมายไม่สำเร็จ");
        closeModal("savings-modal");
        byId("savings-form").reset();
        await fetchSavingsGoals();
    } catch (error) { toast(error.message, "error"); }
}

function renderCategoryManager() {
    if (!categoriesList.length) {
        byId("category-manager-list").innerHTML = '<p class="empty-state">ยังไม่มีหมวดหมู่</p>';
        return;
    }
    byId("category-manager-list").innerHTML = categoriesList.map(item => `
        <div class="category-manager-item" data-category-id="${item.id}">
            <span class="category-dot" style="background:${safeColor(item.color)}"></span>
            <span class="category-name">${escapeHtml(item.name)}</span>
            <span class="category-kind">${item.type === "income" ? "รายรับ" : "รายจ่าย"}</span>
            ${item.user_id ? '<button class="btn-icon" data-category-action="edit"><i class="fa-solid fa-pen"></i></button><button class="btn-icon delete-btn" data-category-action="delete"><i class="fa-solid fa-trash-can"></i></button>' : '<span class="system-badge">ระบบ</span>'}
        </div>`).join("");
}

async function submitCategory(event) {
    event.preventDefault();
    try {
        await requireOk(await authenticatedFetch("/api/categories", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name: byId("category-name").value, type: byId("category-type").value, color: byId("category-color").value, icon: "fa-tag"})
        }), "เพิ่มหมวดหมู่ไม่สำเร็จ");
        byId("category-form").reset();
        byId("category-color").value = "#6B7280";
        await fetchCategories();
    } catch (error) { toast(error.message, "error"); }
}

async function categoryAction(event) {
    const button = event.target.closest("[data-category-action]");
    if (!button) return;
    const id = Number(button.closest("[data-category-id]").dataset.categoryId);
    const category = categoriesList.find(item => item.id === id);
    if (!category) return;
    try {
        if (button.dataset.categoryAction === "delete") {
            if (!confirm(`ลบหมวดหมู่ “${category.name}” หรือไม่?`)) return;
            await requireOk(await authenticatedFetch(`/api/categories/${id}`, {method: "DELETE"}), "ลบหมวดหมู่ไม่สำเร็จ");
        } else {
            const name = prompt("ชื่อหมวดหมู่ใหม่", category.name);
            if (!name) return;
            await requireOk(await authenticatedFetch(`/api/categories/${id}`, {
                method: "PUT", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({name, type: category.type, color: category.color || "#6B7280", icon: category.icon || "fa-tag"})
            }), "แก้ไขหมวดหมู่ไม่สำเร็จ");
        }
        await fetchCategories();
        await Promise.all([fetchDashboardData(), fetchBudgets(), fetchHistory()]);
    } catch (error) { toast(error.message, "error"); }
}

async function fetchHistory() {
    const requestId = ++historyRequestId;
    const params = new URLSearchParams({limit: "200"});
    const values = {
        type: byId("history-type").value,
        start_date: byId("history-start").value,
        end_date: byId("history-end").value,
        search: byId("history-search").value.trim()
    };
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
    byId("history-filter-status").textContent = "กำลังกรองรายการ…";
    const [historyRaw, summaryRaw] = await Promise.all([
        authenticatedFetch(`/api/transactions?${params}`),
        authenticatedFetch(`/api/transactions/summary?${params}`)
    ]);
    const [response, summaryResponse] = await Promise.all([
        requireOk(historyRaw, "โหลดประวัติไม่สำเร็จ"),
        requireOk(summaryRaw, "คำนวณยอดรวมไม่สำเร็จ")
    ]);
    const [rows, summary] = await Promise.all([response.json(), summaryResponse.json()]);
    if (requestId !== historyRequestId) return;
    const appliedType = response.headers.get("x-applied-transaction-type") || "all";
    const expectedType = values.type || "all";
    if (appliedType !== expectedType || (values.type && rows.some(item => item.type !== values.type))) {
        historyTransactions = [];
        renderTransactionList([], byId("history-list"));
        byId("history-filter-status").textContent = `หยุดแสดงข้อมูล: ตัวกรองไม่ตรงกัน (ต้องการ ${expectedType}, API ใช้ ${appliedType})`;
        toast("ระบบหยุดรายการที่ประเภทไม่ตรงกับตัวกรอง กรุณารีเฟรชหน้า", "error");
        return;
    }
    historyTransactions = rows;
    renderTransactionList(historyTransactions, byId("history-list"));
    const typeLabels = {income: "รายรับ", expense: "รายจ่าย", transfer: "โอนเงิน"};
    const filters = [];
    if (values.type) filters.push(`ประเภท: ${typeLabels[values.type]}`);
    if (values.start_date) filters.push(`ตั้งแต่: ${values.start_date}`);
    if (values.end_date) filters.push(`ถึง: ${values.end_date}`);
    if (values.search) filters.push(`ค้นหา: “${values.search}”`);
    const shown = summary.count > rows.length ? ` (แสดง ${rows.length.toLocaleString("th-TH")} รายการล่าสุด)` : "";
    byId("history-filter-status").textContent = `พบ ${summary.count.toLocaleString("th-TH")} รายการ${shown} · ${filters.length ? filters.join(" · ") : "แสดงทั้งหมด"}`;
    byId("history-total-income").textContent = money(summary.income);
    byId("history-total-expense").textContent = money(summary.expense);
    byId("history-total-net").textContent = money(summary.net);
    byId("history-total-net").className = summary.net >= 0 ? "income-text" : "expense-text";
}

function exportPdf() {
    const params = new URLSearchParams();
    if (byId("history-start").value) params.set("start_date", byId("history-start").value);
    if (byId("history-end").value) params.set("end_date", byId("history-end").value);
    if (byId("history-type").value) params.set("transaction_type", byId("history-type").value);
    window.location.href = `/api/reports/transactions.pdf?${params}`;
}

async function fetchLineStatus() {
    const response = await requireOk(await authenticatedFetch("/api/reports/line-status"), "โหลดสถานะ LINE ไม่สำเร็จ");
    const data = await response.json();
    byId("unlink-line-btn").classList.toggle("hidden", !data.paired);
    byId("get-pairing-code-btn").classList.toggle("hidden", data.paired);
    if (data.paired) byId("line-pair-desc").textContent = "เชื่อมต่อ LINE แล้ว ทุกข้อมูลจาก AI ต้องได้รับการยืนยันก่อนบันทึก";
}

async function fetchPairingCode() {
    const button = byId("get-pairing-code-btn");
    button.disabled = true;
    try {
        const response = await requireOk(await authenticatedFetch("/api/reports/pairing-code", {method: "POST"}), "ขอรหัสไม่สำเร็จ");
        const data = await response.json();
        const box = byId("pairing-code-box");
        box.textContent = data.code;
        box.classList.remove("hidden");
        button.classList.add("hidden");
        box.onclick = async () => {
            await navigator.clipboard.writeText(data.code);
            toast("คัดลอกรหัสแล้ว");
        };
        let seconds = data.expires_in_seconds;
        clearInterval(pairingCodeTimer);
        pairingCodeTimer = setInterval(() => {
            seconds -= 1;
            if (seconds <= 0) {
                clearInterval(pairingCodeTimer);
                box.classList.add("hidden");
                button.classList.remove("hidden");
                button.disabled = false;
            } else {
                const min = Math.floor(seconds / 60);
                const sec = String(seconds % 60).padStart(2, "0");
                byId("line-pair-desc").textContent = `ส่ง “ผูกบัญชี ${data.code}” ให้ LINE Bot ภายใน ${min}:${sec}`;
            }
        }, 1000);
    } catch (error) {
        button.disabled = false;
        toast(error.message, "error");
    }
}

async function unlinkLine() {
    if (!confirm("ยกเลิกการเชื่อมต่อ LINE หรือไม่?")) return;
    try {
        await requireOk(await authenticatedFetch("/api/reports/line-pairing", {method: "DELETE"}), "ยกเลิกไม่สำเร็จ");
        byId("line-pair-desc").textContent = "จับคู่บัญชีกับ LINE เพื่อบันทึกรายการได้รวดเร็ว";
        await fetchLineStatus();
    } catch (error) { toast(error.message, "error"); }
}
