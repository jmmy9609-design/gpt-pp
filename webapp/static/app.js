const form = document.getElementById("extractForm");
const credential = document.getElementById("credential");
const proxy = document.getElementById("proxy");
const submitBtn = document.getElementById("submitBtn");
const clearBtn = document.getElementById("clearBtn");
const message = document.getElementById("message");
const visitorId = (() => {
  const key = "plus_gate_visitor_id";
  let value = localStorage.getItem(key);
  if (!value) {
    value = (crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    localStorage.setItem(key, value);
  }
  return value;
})();

function apiHeaders(extra = {}) {
  return {
    "X-Visitor-ID": visitorId,
    ...extra
  };
}

// 持久化载入动态家宽代理池
if (proxy) {
  const savedProxy = localStorage.getItem("plus_gate_proxy");
  if (savedProxy) {
    proxy.value = savedProxy;
  }
  // 监听并实时保存代理更改
  proxy.addEventListener("input", (e) => {
    localStorage.setItem("plus_gate_proxy", e.target.value.trim());
  });
}

const testProxyBtn = document.getElementById("testProxyBtn");
const proxyCheckResult = document.getElementById("proxyCheckResult");

if (testProxyBtn && proxyCheckResult) {
  testProxyBtn.addEventListener("click", async () => {
    const proxyVal = proxy.value.trim();
    if (!proxyVal) {
      proxyCheckResult.className = "proxy-feedback-alert failed";
      proxyCheckResult.innerHTML = "⚠️ 请先填写至少一个代理，再执行测试！";
      return;
    }
    
    // 开启 loading 状态
    testProxyBtn.disabled = true;
    const originalText = testProxyBtn.innerHTML;
    testProxyBtn.innerHTML = "正在检测...";
    proxyCheckResult.className = "proxy-feedback-alert";
    proxyCheckResult.style.display = "none";
    
    try {
      const resp = await fetch("/api/test-proxy", {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ proxy: proxyVal })
      });
      
      const data = await resp.json().catch(() => ({
        ok: false,
        message: `代理测试异常 (HTTP ${resp.status})，服务器未能在规定时间内连通该代理。`
      }));
      
      if (data.ok) {
        proxyCheckResult.className = "proxy-feedback-alert success";
        proxyCheckResult.style.display = "block";
        proxyCheckResult.innerHTML = `🟢 <b>代理池可用！</b><br/>可用出口: <code>${data.ip}</code><br/>候选数量: ${data.candidates || 1}<br/>网络节点: ${data.org || "未知"}<br/>响应延迟: <b>${data.latency_ms}ms</b>`;
      } else {
        proxyCheckResult.className = "proxy-feedback-alert failed";
        proxyCheckResult.style.display = "block";
        proxyCheckResult.innerHTML = `🔴 <b>代理不可用！</b><br/>诊断分析: ${data.message}`;
      }
    } catch (err) {
      proxyCheckResult.className = "proxy-feedback-alert failed";
      proxyCheckResult.style.display = "block";
      proxyCheckResult.innerHTML = `🔴 <b>网络连接故障：</b>无法与探测网关建立通信 (${err.message})`;
    } finally {
      testProxyBtn.disabled = false;
      testProxyBtn.innerHTML = originalText;
    }
  });
}

// 新结构元素
const progressBar = document.getElementById("progressBar");
const progressCount = document.getElementById("progressCount");
const progressPercent = document.getElementById("progressPercent");
const resultPanel = document.getElementById("resultPanel");
const resultTableBody = document.getElementById("resultTableBody");
const exportBtn = document.getElementById("exportBtn");
const cityStatsSummary = document.getElementById("cityStatsSummary");

// 导入文本事件
const importTxtBtn = document.getElementById("importTxtBtn");
const fileInput = document.getElementById("fileInput");

// 点击导入按钮，触发隐藏的 file input
importTxtBtn.addEventListener("click", () => {
  fileInput.click();
});

// 处理 TXT 导入读取
fileInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  
  const reader = new FileReader();
  reader.onload = function(evt) {
    credential.value = evt.target.result;
    const linesCount = evt.target.result.split("\n").map(l => l.trim()).filter(l => l.length > 0).length;
    setMessage(`成功导入外部通道数据: "${file.name}"，共载入 ${linesCount} 笔交易凭证。`, "ok");
    credential.focus();
  };
  reader.readAsText(file);
});

function setMessage(text, kind = "idle") {
  message.textContent = text;
  message.className = `message ${kind}`;
}

// 进度条与状态控制
function updateProgress(percent, countText) {
  progressBar.style.width = `${percent}%`;
  progressPercent.textContent = `${percent}%`;
  progressCount.textContent = countText;
}

function resetProgress() {
  updateProgress(0, "网关引擎就绪，等待事务触发。");
  setMessage("网关空闲。", "idle");
  resultPanel.classList.add("hidden");
  resultTableBody.innerHTML = "";
  if (cityStatsSummary) cityStatsSummary.textContent = "城市胜率统计等待采样。";
  exportBtn.classList.add("hidden");
}

// 智能辅助：从单行可能包含 JSON 的数据里尝试提取 accessToken 核心内容
function tryExtractTokenFromLine(line) {
  const text = line.trim();
  if (!text) return "";
  if (text.startsWith("{")) {
    try {
      const data = JSON.parse(text);
      const nested = collectTokensFromValue(data);
      if (nested.length > 0) return nested[0];
    } catch (e) {
      // 解析失败则继续走正则兜底；不要把整段 Session JSON 当 token。
    }
  }
  const found = text.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
  return found ? found[0] : "";
}

function collectTokensFromValue(value, out = []) {
  if (!value) return out;
  if (Array.isArray(value)) {
    value.forEach(item => collectTokensFromValue(item, out));
    return out;
  }
  if (typeof value === "object") {
    Object.entries(value).forEach(([key, child]) => {
      if (key === "accessToken" || key === "access_token") {
        const trimmed = typeof child === "string" ? child.trim() : "";
        if (/^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(trimmed)) {
          out.push(trimmed);
        }
      } else {
        collectTokensFromValue(child, out);
      }
    });
  }
  return out;
}

function parseCredentialInput(rawText) {
  const text = rawText.trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    const tokens = collectTokensFromValue(parsed);
    if (tokens.length > 0) return Array.from(new Set(tokens));
  } catch (e) {
    // Fall through to line/regex parsing.
  }
  const fromLines = text.split("\n").map(l => tryExtractTokenFromLine(l)).filter(t => t.length > 0);
  const fromText = text.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g) || [];
  return Array.from(new Set([...fromLines, ...fromText]));
}

// 批量流式执行任务：Session JSON 只提取 accessToken；账号层并发，单账号内部串行换代理。
async function extractLink(event) {
  event.preventDefault();
  resetProgress();

  if (credential.value.trim().length === 0) {
    setMessage("请输入 API 凭证或导入 TXT 文件以启动引擎。", "error");
    return;
  }

  const tokens = parseCredentialInput(credential.value);
  if (tokens.length === 0) {
    setMessage("未解析到任何合法的 JWT 授权密钥；Session JSON 只会读取 accessToken 字段，其它字段全部丢弃。", "error");
    return;
  }

  const proxyValue = proxy.value.trim();
  if (!proxyValue) {
    setMessage("必须填写代理；系统不会直连。", "error");
    return;
  }

  submitBtn.disabled = true;
  const originalBtnText = submitBtn.textContent;
  submitBtn.innerHTML = `<span class="btn-loader">
    <svg class="icon spinner-icon" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="rgba(255,255,255,0.2)"></circle><path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor"></path></svg>
    正在批量转化...
  </span>`;

  resultPanel.classList.remove("hidden");
  setMessage(`Python 网关已启动：账号层并发处理，单账号串行换代理，401/TLS/timeout 分层隔离。`, "idle");

  const resultByIndex = new Map();
  tokens.forEach((token, i) => createResultRow(token, i + 1));

  let finishedCount = 0;
  const markFinished = () => {
    finishedCount += 1;
    const progressPercentValue = Math.round((finishedCount / tokens.length) * 100);
    updateProgress(progressPercentValue, `批量处理中：已完成 ${finishedCount} / ${tokens.length}`);
  };

  function createResultRow(token, index) {
    const truncatedToken = token.length > 20 ? `${token.substring(0, 10)}...${token.substring(token.length - 10)}` : token;
    const row = document.createElement("tr");
    row.id = `row-${index}`;
    row.innerHTML = `
      <td class="col-idx">${index}</td>
      <td class="token-cell" title="${token}">${truncatedToken}</td>
      <td id="amount-${index}" class="col-amt">-</td>
      <td class="col-status">
        <span id="badge-${index}" class="badge-status processing">
          <svg class="icon spinner-icon" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="rgba(255,255,255,0.2)"></circle><path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor"></path></svg>
          转化中
        </span>
      </td>
      <td id="action-${index}" class="col-action"><span class="error-text">Python HTTP 网关转化中...</span></td>
    `;
    resultTableBody.appendChild(row);
  }

  function upsertResult(index, value) {
    resultByIndex.set(index, value);
  }

  function renderCityStats(stats = []) {
    if (!cityStatsSummary) return;
    const rows = Array.isArray(stats) ? stats.filter(item => item && item.city) : [];
    if (rows.length === 0) {
      cityStatsSummary.textContent = "城市胜率统计等待采样。";
      return;
    }
    const top = rows.slice(0, 4).map(item => {
      const rate = typeof item.success_rate === "number" ? `${item.success_rate}%` : "-";
      return `<strong>${item.city}</strong> ${rate} (${item.success || 0}/${item.attempts || 0})`;
    }).join("　");
    cityStatsSummary.innerHTML = `城市胜率：${top}`;
  }

  function renderSuccess(token, index, data) {
    const result = data.result || data;
    const badge = document.getElementById(`badge-${index}`);
    const amountTd = document.getElementById(`amount-${index}`);
    const actionTd = document.getElementById(`action-${index}`);
    if (!badge || !amountTd || !actionTd) return;
    badge.className = "badge-status success";
    badge.textContent = "成功建立";
    amountTd.innerHTML = `🟢 ${result.amount_display || "0.00 USD"}`;
    const payUrl = result.paypal_authorize_url || "";
    actionTd.innerHTML = `
      <div class="flex-center-gap">
        <button type="button" class="mini-btn" id="copy-btn-${index}">复制通道</button>
        <input type="hidden" id="url-val-${index}" value="${payUrl}" />
      </div>
    `;
    const copyBtn = document.getElementById(`copy-btn-${index}`);
    copyBtn.addEventListener("click", async () => {
      await navigator.clipboard.writeText(payUrl);
      const originalText = copyBtn.textContent;
      copyBtn.classList.add("success");
      copyBtn.innerHTML = `✓ 已复制`;
      setTimeout(() => {
        copyBtn.classList.remove("success");
        copyBtn.textContent = originalText;
      }, 1500);
    });
    const previous = resultByIndex.get(index);
    upsertResult(index, { index, ok: true, token, url: payUrl });
    if (!previous || !previous.ok) {
      incrementGlobalCounter();
    }
  }

  function renderFailed(token, index, data, label = "转化失败") {
    const result = data.result || data || {};
    const badge = document.getElementById(`badge-${index}`);
    const amountTd = document.getElementById(`amount-${index}`);
    const actionTd = document.getElementById(`action-${index}`);
    if (!badge || !amountTd || !actionTd) return;
    badge.className = "badge-status failed";
    badge.textContent = label;
    amountTd.textContent = result.amount_display || "unknown";
    actionTd.innerHTML = `<span class="error-text">${data.error || result.message || "本次未拿到有效 PayPal 授权链接"}</span>`;
    upsertResult(index, { index, ok: false, token, error: data.error || result.message });
  }

  function renderRow(row) {
    const index = row.index;
    const token = tokens[index - 1] || "";
    if (row.ok || (row.result && row.result.ok)) {
      renderSuccess(token, index, row);
    } else {
      renderFailed(token, index, row);
    }
    markFinished();
  }

  async function streamBatch() {
    const response = await fetch("/api/extract-batch", {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json", "Accept": "application/x-ndjson" }),
      body: JSON.stringify({ tokens, proxy: proxyValue })
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({ message: `网关异常 HTTP ${response.status}` }));
      throw new Error(data.message || `网关异常 HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        const row = JSON.parse(line);
        if (row.type === "progress") {
          setMessage(row.message || "代理预热中...", "idle");
          if (row.city_stats) renderCityStats(row.city_stats);
          continue;
        }
        if (row.type === "stats") {
          renderCityStats(row.city_stats);
          continue;
        }
        renderRow(row);
      }
    }
    const tail = buffer.trim();
    if (tail) {
      const row = JSON.parse(tail);
      if (row.type === "progress") {
        setMessage(row.message || "代理预热中...", "idle");
        if (row.city_stats) renderCityStats(row.city_stats);
      } else if (row.type === "stats") {
        renderCityStats(row.city_stats);
      } else {
        renderRow(row);
      }
    }
  }

  async function fallbackClientBatch() {
    const concurrency = Math.min(12, tokens.length);
    let nextIndex = 0;
    async function one(token, index) {
      const response = await fetch("/api/extract", {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json", "Accept": "application/json" }),
        body: JSON.stringify({ credential: token, proxy: proxyValue })
      });
      const data = await response.json().catch(() => ({ message: `网关异常 HTTP ${response.status}` }));
      renderRow({ index, ok: response.ok && data.ok, result: data, error: data.message });
    }
    const workers = Array.from({ length: concurrency }, async () => {
      while (nextIndex < tokens.length) {
        const i = nextIndex++;
        try {
          await one(tokens[i], i + 1);
        } catch (err) {
          renderFailed(tokens[i], i + 1, { error: `通道通信中断: ${err.message || err}` });
          markFinished();
        }
      }
    });
    await Promise.all(workers);
  }

  try {
    try {
      await streamBatch();
    } catch (err) {
      console.warn("批量流式接口失败，降级到前端并发:", err);
      if (finishedCount === 0) {
        setMessage(`批量流式接口异常，已自动降级到前端并发：${err.message || err}`, "idle");
        await fallbackClientBatch();
      } else {
        throw err;
      }
    }
  } finally {
    updateProgress(100, `网关事务处理完毕：共完成 ${finishedCount} / ${tokens.length} 通道转换`);
    const successCount = Array.from(resultByIndex.values()).filter(r => r.ok).length;
    const failCount = tokens.length - successCount;
    setMessage(`批量转化完成。转化成功：${successCount} 通道，失败：${failCount} 通道。`, successCount > 0 ? "ok" : "error");
    if (successCount > 0) {
      exportBtn.classList.remove("hidden");
      exportBtn.onclick = () => {
        const successUrls = Array.from(resultByIndex.values()).filter(r => r.ok).map(r => r.url).join("\n");
        const blob = new Blob([successUrls], { type: "text/plain;charset=utf-8" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `PayPal_Gateway_Channels_${new Date().toISOString().slice(0,10)}.txt`;
        link.click();
      };
    }
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalBtnText;
  }
}

form.addEventListener("submit", extractLink);

clearBtn.addEventListener("click", () => {
  credential.value = "";
  // 代理由于是高规格静态常驻配置，重置时在界面与本地持久层中均特予以保留
  resetProgress();
  setMessage("网关配置与事务日志重置，通道就绪。", "idle");
  credential.focus();
});

// === 全站提链计数与在线同步引擎 (SaaS Real-time Sync Engine) ===
const globalCounterEl = document.getElementById("globalCounter");
const onlineCounterEl = document.getElementById("onlineCounter");
let currentGlobalCount = -1; // 默认初始化为 -1 确保 0 也能作为初始成功更新

// 格式化输出数字千分位
function formatNumberWithCommas(num) {
  return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function renderGlobalCounter() {
  if (globalCounterEl && currentGlobalCount >= 0) {
    globalCounterEl.textContent = formatNumberWithCommas(currentGlobalCount);
  }
}

// 同步和拉取后端真实数据的核心异步函数
async function syncGlobalCounter() {
  try {
    const response = await fetch("/api/stats", { headers: apiHeaders() });
    const data = await response.json();
    if (data && data.ok) {
      // 1. 同步全站累计成功
      if (typeof data.success_count === "number") {
        const newCount = data.success_count;
        if (newCount !== currentGlobalCount) {
          const isInitial = (currentGlobalCount === -1);
          currentGlobalCount = newCount;
          renderGlobalCounter();
          
          // 只有在非初次加载且确实有增长（其他并发用户或自己成功提链）时，才触发高频震动微动效
          if (!isInitial) {
            const statsCard = document.querySelector(".stats-float-card");
            if (statsCard) {
              statsCard.style.transform = "scale(1.05)";
              statsCard.style.borderColor = "rgba(16, 185, 129, 0.4)";
              setTimeout(() => {
                statsCard.style.transform = "";
                statsCard.style.borderColor = "";
              }, 300);
            }
          }
        }
      }
      
      // 2. 同步全站实时在线
      if (typeof data.online_count === "number" && onlineCounterEl) {
        onlineCounterEl.textContent = formatNumberWithCommas(data.online_count);
      }
      if (Array.isArray(data.city_stats) && cityStatsSummary) {
        const rows = data.city_stats.slice(0, 4);
        cityStatsSummary.innerHTML = rows.length
          ? `城市胜率：${rows.map(item => `<strong>${item.city}</strong> ${item.success_rate}% (${item.success}/${item.attempts})`).join("　")}`
          : "城市胜率统计等待采样。";
      }
    }
  } catch (err) {
    console.error("同步全站真实转化数据失败:", err);
  }
}

// 提链成功时的抢先本地增加函数 (提供 0 延迟的完美交互反馈)
function incrementGlobalCounter() {
  if (currentGlobalCount < 0) currentGlobalCount = 0;
  currentGlobalCount += 1;
  renderGlobalCounter();
  
  // 抢先微动
  const statsCard = document.querySelector(".stats-float-card");
  if (statsCard) {
    statsCard.style.transform = "scale(1.05)";
    statsCard.style.borderColor = "rgba(16, 185, 129, 0.4)";
    setTimeout(() => {
      statsCard.style.transform = "";
      statsCard.style.borderColor = "";
    }, 300);
  }
}

// 首次拉取
syncGlobalCounter();

// 每 6 秒高频拉取一次最新的后端持久化库真实成功总量 (完美支持多用户全站并发运营)
setInterval(syncGlobalCounter, 6000);
