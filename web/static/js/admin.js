/* 后台通用工具 */
async function api(url, options = {}) {
  const opts = Object.assign({ headers: {} }, options);
  if (opts.body instanceof FormData) {
    // 浏览器自动设置 multipart 边界
  } else if (opts.body && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(url, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { data = {}; }
  if (resp.status === 401) {
    location.href = '/admin/login?next=' + encodeURIComponent(location.pathname);
    throw new Error('未登录');
  }
  if (!resp.ok || data.success === false) {
    throw new Error((data && (data.message || data.msg)) || ('请求失败 ' + resp.status));
  }
  return data;
}

function toast(message, type = 'info') {
  let box = document.querySelector('.toast');
  if (!box) {
    box = document.createElement('div');
    box.className = 'toast';
    document.body.appendChild(box);
  }
  const item = document.createElement('div');
  item.className = 'item ' + type;
  item.textContent = message;
  box.appendChild(item);
  setTimeout(() => item.remove(), 3200);
}

function esc(v) {
  return String(v == null ? '' : v)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

function fmtMoney(v) {
  const n = Number(v || 0);
  return n.toFixed(2);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('已复制到剪贴板', 'success');
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); toast('已复制到剪贴板', 'success'); }
    catch (e2) { toast('复制失败，请手动选择', 'error'); }
    ta.remove();
  }
}

const STATUS_TEXT = { 0: '待支付', 1: '已支付', 2: '已过期' };
const STATUS_CLASS = { 0: 'orange', 1: 'green', 2: 'red' };

const SHOP_STATUS_TEXT = {
  pending: '待支付', paid: '已支付', fulfilled: '已发货',
  expired: '已过期', failed: '发货失败',
};
const SHOP_STATUS_CLASS = {
  pending: 'orange', paid: 'blue', fulfilled: 'green',
  expired: 'red', failed: 'red',
};
