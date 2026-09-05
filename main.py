# -*- coding: utf-8 -*-
"""
山寨币情报预警系统（单文件版）
用法：
  python main.py          # 实时扫描（每10分钟）
  python main.py digest   # 每日摘要（每天一次）
依赖：requests
"""
import json
import os
import sys
import time
import traceback
import requests

# ==================== 配置 ====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
EXCHANGE_SNAPSHOT_FILE = os.path.join(BASE_DIR, "exchange_snapshot.json")

RADAR_TOP_N = 250
RADAR_MIN_VOLUME_USD = 5_000_000
RADAR_MIN_MCAP = 10_000_000
RADAR_MAX_MCAP = 20_000_000_000
RADAR_1H_GAIN = 8.0
RADAR_24H_GAIN = 20.0

EXCLUDE_SYMBOLS = {
    "usdt", "usdc", "dai", "busd", "tusd", "usde", "fdusd", "usdp",
    "frax", "lusd", "gusd", "susd", "usdd", "ustc", "ust", "mim", "pyusd",
    "weth", "wbtc", "steth", "cbeth", "weeth", "reth", "wsteth",
}

NEW_PAIR_MAX_AGE_HOURS = 24
NEW_PAIR_MIN_LIQUIDITY = 50_000
NEW_PAIR_MIN_VOLUME = 100_000
NEW_PAIR_MIN_GAIN = 30.0

MEME_LIQ_HIGH = 200_000
MEME_LIQ_MID = 50_000
MEME_TOP10_OK = 0.30
MEME_TOP10_WARN = 0.50
MEME_HOLDERS_HIGH = 2000
MEME_HOLDERS_MID = 500
MEME_TURNOVER = 3.0
MEME_GAIN_LOW = 50
MEME_GAIN_HIGH = 500
MEME_AGE_LOW = 6
MEME_AGE_HIGH = 48

TROUGH_ATH_DROP = -75.0
TROUGH_TURNOVER = 50.0

SECTOR_EXCLUDE = {"Smart Contract Platform", "Layer 1 (L1)"}
SECTOR_TOP_N = 6

SNAPSHOT_TTL = 24 * 3600
DAILY_ALERT_CAP = 5
HTTP_TIMEOUT = 15
USER_AGENT = "altintel/1.0"

REVMAP = {
    "uniswap": {"defillama": "uniswap", "name": "Uniswap", "sector": "DEX"},
    "aave": {"defillama": "aave", "name": "Aave", "sector": "借贷"},
    "ethena": {"defillama": "ethena", "name": "Ethena", "sector": "稳定币/DeFi"},
    "lido-dao": {"defillama": "lido", "name": "Lido", "sector": "流动性质押"},
    "jupiter-exchange-solana": {"defillama": "jupiter", "name": "Jupiter", "sector": "DEX聚合"},
    "hyperliquid": {"defillama": "hyperliquid", "name": "Hyperliquid", "sector": "永续DEX"},
    "ondo-finance": {"defillama": "ondo-finance", "name": "Ondo Finance", "sector": "RWA"},
    "morpho": {"defillama": "morpho", "name": "Morpho", "sector": "借贷"},
    "raydium": {"defillama": "raydium", "name": "Raydium", "sector": "DEX"},
    "jito-governance-token": {"defillama": "jito", "name": "Jito", "sector": "MEV/质押"},
    "pancakeswap-token": {"defillama": "pancakeSwap", "name": "PancakeSwap", "sector": "DEX"},
    "aerodrome-finance": {"defillama": "aerodrome", "name": "Aerodrome", "sector": "DEX"},
    "gmx": {"defillama": "gmx", "name": "GMX", "sector": "永续DEX"},
    "dydx-chain": {"defillama": "dydx", "name": "dYdX", "sector": "永续DEX"},
    "sky": {"defillama": "sky", "name": "Sky (Maker)", "sector": "RWA/借贷"},
    "pendle": {"defillama": "pendle", "name": "Pendle", "sector": "DeFi/收益"},
    "chainlink": {"defillama": "chainlink", "name": "Chainlink", "sector": "预言机"},
    "syrup": {"defillama": "maple-finance", "name": "Maple/Syrup", "sector": "RWA/借贷"},
    "centrifuge-2": {"defillama": "centrifuge", "name": "Centrifuge", "sector": "RWA"},
}

# ==================== 通知与状态 ====================
def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[notify] Telegram 未配置，仅打印：")
        print(text)
        return True
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                return True
            print(f"[notify] HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[notify] 第 {attempt + 1} 次失败: {e}")
        time.sleep(2 * (attempt + 1))
    return False


def _reset_daily_if_needed(state):
    today = _today()
    if state.get("cap_date") != today:
        state["cap_date"] = today
        state["cap_count"] = 0
        state["sent"] = {}


def alert_under_cap(text, key):
    state = _load_state()
    _reset_daily_if_needed(state)
    if key in state.get("sent", {}):
        return False
    if state["cap_count"] >= DAILY_ALERT_CAP:
        print(f"[notify] 已达每日上限 {DAILY_ALERT_CAP}，丢弃: {key}")
        return False
    ok = send_telegram(text)
    if ok:
        state["cap_count"] += 1
        state["sent"][key] = int(time.time())
        _save_state(state)
    return ok


def alert_always(text, key):
    state = _load_state()
    _reset_daily_if_needed(state)
    forever = state.setdefault("seen_forever", {})
    if key in forever:
        return False
    ok = send_telegram(text)
    if ok:
        forever[key] = int(time.time())
        _save_state(state)
    return ok


def error_alert(context, err):
    state = _load_state()
    _reset_daily_if_needed(state)
    key = f"err:{context}"
    if key in state.get("sent", {}):
        return False
    ok = send_telegram(f"⚠️ 系统告警 [{context}]\n{err}")
    if ok:
        state["sent"][key] = int(time.time())
        _save_state(state)
    return ok


def record_history(key, entry):
    state = _load_state()
    now = int(time.time())
    hist = [h for h in state.get("history", [])
            if now - h.get("ts", 0) < 72 * 3600 and h.get("key") != key]
    hist.append({"key": key, "ts": now, **entry})
    state["history"] = hist
    _save_state(state)


def get_history():
    return _load_state().get("history", [])


# ==================== 数据源 ====================
def _get(url, params=None):
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def coingecko_radar():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd", "order": "market_cap_desc",
        "per_page": RADAR_TOP_N, "page": 1, "sparkline": "false",
        "price_change_percentage": "1h,24h,7d",
    }
    data = _get(url, params)
    out = []
    for c in data:
        sym = (c.get("symbol") or "").lower()
        if sym in EXCLUDE_SYMBOLS:
            continue
        mcap = c.get("market_cap") or 0
        vol = c.get("total_volume") or 0
        if not (RADAR_MIN_MCAP <= mcap <= RADAR_MAX_MCAP):
            continue
        if vol < RADAR_MIN_VOLUME_USD:
            continue
        out.append({
            "id": c["id"], "symbol": sym, "name": c["name"],
            "price": c.get("current_price"), "mcap": mcap,
            "fdv": c.get("fully_diluted_valuation"), "volume_24h": vol,
            "chg_1h": c.get("price_change_percentage_1h_in_currency"),
            "chg_24h": c.get("price_change_percentage_24h_in_currency"),
            "chg_7d": c.get("price_change_percentage_7d_in_currency"),
            "ath_change": c.get("ath_change_percentage"),
        })
    out.sort(key=lambda x: (x["chg_24h"] or 0), reverse=True)
    return out


def _fetch_exchange_symbols():
    res = {"binance_spot": [], "binance_futures": [], "gate_spot": [], "okx_spot": [], "okx_swap": []}
    try:
        d = _get("https://api.binance.com/api/v3/exchangeInfo")
        res["binance_spot"] = sorted({s["baseAsset"].lower() for s in d["symbols"]
                                      if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"})
    except Exception as e:
        print(f"[binance] 现货失败: {e}")
    try:
        d = _get("https://fapi.binance.com/fapi/v1/exchangeInfo")
        res["binance_futures"] = sorted({s["baseAsset"].lower() for s in d["symbols"]
                                         if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL"
                                         and s.get("quoteAsset") == "USDT"})
    except Exception as e:
        print(f"[binance] 合约失败: {e}")
    try:
        d = _get("https://api.gateio.ws/api/v4/spot/currency_pairs")
        res["gate_spot"] = sorted({p["base"].lower() for p in d
                                   if p.get("quote") == "USDT" and p.get("trade_status") == "tradable"})
    except Exception as e:
        print(f"[gate] 失败: {e}")
    try:
        d = _get("https://www.okx.com/api/v5/public/instruments", {"instType": "SPOT"})
        res["okx_spot"] = sorted({i["baseCcy"].lower() for i in d.get("data", []) if i.get("quoteCcy") == "USDT"})
    except Exception as e:
        print(f"[okx] 现货失败: {e}")
    try:
        d = _get("https://www.okx.com/api/v5/public/instruments", {"instType": "SWAP"})
        res["okx_swap"] = sorted({i["instId"].split("-")[0].lower() for i in d.get("data", [])
                                  if i.get("settleCcy") == "USDT"})
    except Exception as e:
        print(f"[okx] 合约失败: {e}")
    return res


def _load_exchange_snapshot():
    try:
        with open(EXCHANGE_SNAPSHOT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_exchange_snapshot(snap):
    with open(EXCHANGE_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)


_DIFF_LABELS = {"gate_spot": "Gate", "okx_spot": "OKX现货", "okx_swap": "OKX合约"}


def refresh_exchange_symbols():
    prev = _load_exchange_snapshot()
    now = time.time()
    if prev and (now - prev.get("ts", 0)) < SNAPSHOT_TTL:
        sets = {k: set(v) for k, v in prev.items() if k != "ts"}
        return sets, []
    fresh = _fetch_exchange_symbols()
    new_listings = []
    if prev:
        for key, label in _DIFF_LABELS.items():
            added = set(fresh.get(key, [])) - set(prev.get(key, []))
            for sym in sorted(added):
                new_listings.append({"exchange": label, "symbol": sym.upper()})
    _save_exchange_snapshot({**{k: list(v) for k, v in fresh.items()}, "ts": now})
    sets = {k: set(v) for k, v in fresh.items()}
    return sets, new_listings


def coingecko_categories():
    try:
        data = _get("https://api.coingecko.com/api/v3/coins/categories")
    except Exception as e:
        print(f"[coingecko] 板块失败: {e}")
        return []
    out = [{"name": c.get("name"), "change_24h": c.get("market_cap_change_24h"),
            "mcap": c.get("market_cap")} for c in data]
    out.sort(key=lambda x: (x["change_24h"] or 0), reverse=True)
    return out


def coingecko_fdv_by_ids(ids):
    if not ids:
        return {}
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "ids": ",".join(ids), "sparkline": "false"}
    try:
        data = _get(url, params)
        return {c["id"]: {"fdv": c.get("fully_diluted_valuation") or c.get("market_cap") or 0,
                          "mcap": c.get("market_cap") or 0} for c in data}
    except Exception as e:
        print(f"[coingecko] 批量取 FDV 失败: {e}")
        return {}


def dexscreener_new_profiles():
    return _get("https://api.dexscreener.com/token-profiles/latest/v1")


def dexscreener_pairs_for(addresses):
    if not addresses:
        return []
    url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(addresses)}"
    try:
        data = _get(url)
        return data.get("pairs", []) if isinstance(data, dict) else []
    except Exception as e:
        print(f"[dexscreener] 查询交易对失败: {e}")
        return []


def binance_listings():
    url = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
    params = {"catalogId": 48, "pageNo": 1, "pageSize": 20}
    data = _get(url, params)
    articles = (data.get("data") or {}).get("articles") or []
    out = []
    for a in articles:
        title = a.get("title") or ""
        if "Will List" in title and "Futures" not in title and "Launchpool" not in title:
            out.append({"id": a.get("id"), "title": title})
    return out


GOPLUS_CHAIN_MAP = {
    "ethereum": "1", "bsc": "56", "polygon": "137", "arbitrum": "42161",
    "optimism": "10", "base": "8453", "avalanche": "43114", "fantom": "250",
    "solana": "solana", "tron": "tron",
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def defillama_revenue(slug):
    url = f"https://api.llama.fi/summary/fees/{slug}"
    try:
        d = _get(url, {"dataType": "dailyRevenue"})
        total30d = _num(d.get("total30d"))
        annualized = _num(d.get("annualized1y"))
        if not annualized and total30d:
            annualized = total30d * 12
        return {"name": d.get("name"), "revenue_30d": total30d,
                "revenue_annualized": annualized, "methodology": d.get("methodologyURL")}
    except Exception as e:
        print(f"[defillama] {slug} 失败: {e}")
        return None


def ps_tier(ps):
    if ps is None or ps <= 0:
        return "无收入"
    if ps < 10:
        return "偏低"
    if ps < 20:
        return "合理"
    if ps < 40:
        return "偏高"
    return "偏贵"


def goplus_check(chain, address):
    gid = GOPLUS_CHAIN_MAP.get((chain or "").lower())
    if not gid:
        return None
    url = f"https://api.gopluslabs.io/api/v1/token_security/{gid}"
    try:
        data = _get(url, {"contract_addresses": address})
        res = (data.get("result") or {}).get(address.lower())
        if not res:
            return None

        def flag(v):
            return str(v).lower() in ("1", "true", "yes")

        buy_tax = _num(res.get("buy_tax"))
        sell_tax = _num(res.get("sell_tax"))
        holders = res.get("holders") or []
        top10 = sum(_num(h.get("percent")) for h in holders[:10])

        dangers = []
        if flag(res.get("is_honeypot")):
            dangers.append("蜜罐")
        if buy_tax >= 0.10 or sell_tax >= 0.10:
            dangers.append(f"税{buy_tax:.0%}/{sell_tax:.0%}")
        if flag(res.get("is_mintable")):
            dangers.append("可增发")
        if flag(res.get("can_take_back_ownership")):
            dangers.append("可收回权限")
        if flag(res.get("hidden_owner")):
            dangers.append("隐藏所有者")

        warnings = []
        if not flag(res.get("is_open_source")):
            warnings.append("未开源")

        return {"dangerous": bool(dangers), "dangers": dangers, "warnings": warnings,
                "holder_count": res.get("holder_count"), "top10": top10}
    except Exception as e:
        print(f"[goplus] {address} 检查失败: {e}")
        return None


# ==================== 信号规则 ====================
def radar_signals(coins):
    sigs = []
    for c in coins:
        if (c.get("chg_1h") or 0) >= RADAR_1H_GAIN or (c.get("chg_24h") or 0) >= RADAR_24H_GAIN:
            sigs.append(c)
    return sigs


def new_token_signals(profiles, profile_limit=40):
    addrs, meta = [], {}
    for p in profiles[:profile_limit]:
        addr = p.get("tokenAddress")
        if not addr:
            continue
        addrs.append(addr)
        meta[addr.lower()] = {"chain": p.get("chainId", "?"), "url": p.get("url", "")}
    sigs, seen = [], set()
    for i in range(0, len(addrs), 30):
        for pair in dexscreener_pairs_for(addrs[i:i + 30]):
            base = pair.get("baseToken") or {}
            addr = (base.get("address") or "").lower()
            if addr in seen or addr not in meta:
                continue
            liq = (pair.get("liquidity") or {}).get("usd") or 0
            vol = (pair.get("volume") or {}).get("h24") or 0
            chg = (pair.get("priceChange") or {}).get("h24") or 0
            created = pair.get("pairCreatedAt") or 0
            age_h = (time.time() * 1000 - created) / 3.6e6 if created else 9999
            if age_h > NEW_PAIR_MAX_AGE_HOURS:
                continue
            if liq < NEW_PAIR_MIN_LIQUIDITY or vol < NEW_PAIR_MIN_VOLUME:
                continue
            if chg < NEW_PAIR_MIN_GAIN:
                continue
            seen.add(addr)
            sigs.append({"symbol": (base.get("symbol") or "?").upper(), "chain": meta[addr]["chain"],
                         "address": addr, "liq": liq, "vol": vol, "chg": chg, "age_h": age_h,
                         "price": pair.get("priceUsd") or 0, "url": pair.get("url") or meta[addr]["url"]})
    sigs.sort(key=lambda x: x["vol"], reverse=True)
    return sigs


def meme_score(safety, liq, vol, chg, age_h):
    score = 0
    if safety is not None and not safety["dangerous"]:
        score += 20 if not safety["warnings"] else 10
    if liq >= MEME_LIQ_HIGH:
        score += 20
    elif liq >= MEME_LIQ_MID:
        score += 10
    if safety is not None:
        if safety["top10"] < MEME_TOP10_OK:
            score += 20
        elif safety["top10"] < MEME_TOP10_WARN:
            score += 10
    if safety is not None and safety.get("holder_count"):
        try:
            hc = int(safety["holder_count"])
            if hc > MEME_HOLDERS_HIGH:
                score += 20
            elif hc > MEME_HOLDERS_MID:
                score += 10
        except (TypeError, ValueError):
            pass
    if liq and (vol / liq) >= MEME_TURNOVER:
        score += 10
    if MEME_GAIN_LOW <= chg <= MEME_GAIN_HIGH:
        score += 5
    if MEME_AGE_LOW <= age_h <= MEME_AGE_HIGH:
        score += 5
    return score


def meme_grade(score):
    if score >= 80:
        return "潜力高"
    if score >= 60:
        return "中等"
    return "一般"


def value_trough_signals(coins):
    out = []
    for c in coins:
        ath = c.get("ath_change")
        if ath is None or ath > TROUGH_ATH_DROP:
            continue
        turn = (c["volume_24h"] / c["mcap"] * 100) if c["mcap"] else 0
        if turn < TROUGH_TURNOVER:
            continue
        out.append({**c, "turnover": turn})
    out.sort(key=lambda x: x["ath_change"])
    return out


# ==================== 格式化 ====================
def _pct(x, digits=1):
    return f"{(x or 0):+.{digits}f}%"


def _ex_tag(symbol, ex):
    s = (symbol or "").lower()
    parts = []
    if s in ex["binance_futures"]:
        parts.append("币安合约")
    if s in ex["binance_spot"]:
        parts.append("币安现货")
    if s in ex["gate_spot"]:
        parts.append("Gate")
    if s in ex["okx_spot"]:
        parts.append("OKX现货")
    if s in ex["okx_swap"]:
        parts.append("OKX合约")
    return ("✅ " + "、".join(parts)) if parts else "❌ 未上三大所（仅DEX，埋伏标的）"


def _enrich_ps(c):
    entry = REVMAP.get(c["id"])
    if not entry:
        return
    rev = defillama_revenue(entry["defillama"])
    if rev and rev["revenue_annualized"]:
        c["ps"] = (c.get("fdv") or c["mcap"]) / rev["revenue_annualized"]
        c["ps_rev"] = rev["revenue_annualized"]


def _fmt_radar(c):
    turn = (c['volume_24h'] / c['mcap'] * 100) if c['mcap'] else 0
    fdv = c.get('fdv') or c['mcap']
    lines = [
        "🚀 起飞信号",
        f"{c['name']} ({c['symbol'].upper()})",
        f"24h {_pct(c['chg_24h'])} | 1h {_pct(c['chg_1h'])}",
        f"市值 ${c['mcap']/1e6:.0f}M | FDV ${fdv/1e6:.0f}M | 换手 {turn:.0f}%",
        f"距ATH {(c['ath_change'] or 0):+.1f}%",
        f"交易所: {c.get('ex_tag', '?')}",
    ]
    if c.get("ps"):
        lines.append(f"P/S {c['ps']:.0f}x（{ps_tier(c['ps'])}）| 年化协议收入 ${c['ps_rev']/1e6:.0f}M")
    return "\n".join(lines)


def _fmt_new(s):
    safety = s.get("safety")
    if safety is None:
        safe_line = "安全: ⚠️安全数据暂缺（新链或未索引，无法验证）"
    elif safety["dangerous"]:
        safe_line = "安全: ❌危险(" + "、".join(safety["dangers"]) + ") — 一票否决"
    else:
        warn = ("（" + "、".join(safety["warnings"]) + "）" if safety["warnings"] else "")
        safe_line = f"安全: ✅通过{warn} | 持有人 {safety['holder_count']} | 前10集中 {safety['top10']:.0%}"
    score = s.get("score", 0)
    grade = "安全未验证" if safety is None else meme_grade(score)
    return "\n".join([
        "🆕 新币起飞",
        f"{s['symbol']} ({s['chain']})",
        f"24h {_pct(s['chg'], 0)} | 流动性 ${s['liq']/1e3:.0f}K | 24h量 ${s['vol']/1e3:.0f}K",
        safe_line,
        f"Meme潜力 {score}分（{grade}）",
        f"交易所: {s.get('ex_tag', '?')}",
        f"年龄 {s['age_h']:.1f}h",
        s['url'],
    ])


# ==================== 实时扫描 ====================
def run_scan():
    started = time.time()
    radar, new, listing = [], [], []
    ex, new_listings = refresh_exchange_symbols()

    try:
        for c in radar_signals(coingecko_radar()):
            c["ex_tag"] = _ex_tag(c["symbol"], ex)
            _enrich_ps(c)
            radar.append({"text": _fmt_radar(c), "key": f"radar:{c['id']}", "type": "radar",
                          "symbol": c["symbol"].upper(), "name": c["name"],
                          "price": c.get("price"), "cg_id": c["id"]})
    except Exception as e:
        error_alert("coingecko", f"{e}\n{traceback.format_exc(limit=1)}")

    try:
        profiles = dexscreener_new_profiles()
        cands = []
        for s in new_token_signals(profiles):
            s["safety"] = goplus_check(s["chain"], s["address"])
            if s["safety"] and s["safety"]["dangerous"]:
                continue
            s["score"] = meme_score(s["safety"], s["liq"], s["vol"], s["chg"], s["age_h"])
            s["ex_tag"] = _ex_tag(s["symbol"], ex)
            cands.append(s)
        cands.sort(key=lambda x: x["score"], reverse=True)
        for s in cands:
            new.append({"text": _fmt_new(s), "key": f"new:{s['chain']}:{s['address']}", "type": "new",
                        "symbol": s["symbol"], "name": s["symbol"], "price": s.get("price"),
                        "chain": s["chain"], "address": s["address"]})
    except Exception as e:
        error_alert("dexscreener", f"{e}\n{traceback.format_exc(limit=1)}")

    try:
        for l in binance_listings():
            listing.append({"text": f"📢 币安上币\n{l['title']}", "key": f"listing:{l['id']}",
                            "type": "listing", "symbol": "", "name": l["title"]})
    except Exception as e:
        error_alert("binance", f"{e}\n{traceback.format_exc(limit=1)}")
    for nl in new_listings[:20]:
        listing.append({"text": f"📢 新上币 {nl['exchange']}\n{nl['symbol']}",
                        "key": f"newlist:{nl['exchange']}:{nl['symbol']}",
                        "type": "listing", "symbol": nl["symbol"], "name": nl["exchange"]})

    for a in listing:
        alert_always(a["text"], a["key"])

    sent = 0
    for a in new:
        if sent >= DAILY_ALERT_CAP:
            break
        if alert_under_cap(a["text"], a["key"]):
            sent += 1
            record_history(a["key"], {"type": a["type"], "symbol": a["symbol"],
                                      "name": a["name"], "price": a.get("price"),
                                      "address": a.get("address")})
    for a in radar:
        if sent >= DAILY_ALERT_CAP:
            break
        if alert_under_cap(a["text"], a["key"]):
            sent += 1
            record_history(a["key"], {"type": a["type"], "symbol": a["symbol"],
                                      "name": a["name"], "price": a.get("price"),
                                      "cg_id": a.get("cg_id")})

    print(f"[scan] 耗时 {time.time()-started:.1f}s | 上币{len(listing)} 新币{len(new)} 雷达{len(radar)} | 实时推送{sent}条(不含上币)")
    return 0


# ==================== 每日摘要 ====================
def _value_scan():
    if not REVMAP:
        return []
    fdvs = coingecko_fdv_by_ids(list(REVMAP.keys()))
    rows = []
    for cid, entry in REVMAP.items():
        fdv = fdvs.get(cid, {}).get("fdv")
        if not fdv:
            continue
        rev = defillama_revenue(entry["defillama"])
        if not rev or not rev["revenue_annualized"]:
            continue
        ps = fdv / rev["revenue_annualized"]
        rows.append((ps, entry["name"], entry["sector"], rev["revenue_annualized"]))
    rows.sort(key=lambda x: x[0])
    return rows


def _fmt_mcap(v):
    if not v:
        return "$0"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v/1e3:.0f}K"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _review_signals(coins):
    hist = get_history()
    if not hist:
        return [], {}
    rows, up, down, gone = [], 0, 0, 0
    for h in hist:
        sym = h.get("symbol") or "?"
        chg = None
        if h.get("type") == "radar":
            cur = next((x for x in coins if x["id"] == h.get("cg_id")
                        or x["symbol"].lower() == sym.lower()), None)
            if cur and cur.get("price") and _f(h.get("price")):
                chg = (_f(cur["price"]) / _f(h["price"]) - 1) * 100
            elif cur is None:
                gone += 1
        elif h.get("type") == "new" and h.get("address"):
            pairs = dexscreener_pairs_for([h["address"]])
            if pairs and _f(pairs[0].get("priceUsd")) and _f(h.get("price")):
                chg = (_f(pairs[0]["priceUsd"]) / _f(h["price"]) - 1) * 100
            elif not pairs:
                gone += 1
        if chg is not None:
            up += 1 if chg > 0 else 0
            down += 1 if chg <= 0 else 0
        rows.append((sym, chg))
    return rows, {"up": up, "down": down, "gone": gone, "total": len(rows)}


def run_digest():
    try:
        lines = ["📊 每日山寨情报摘要", f"日期 {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC", ""]

        cats = coingecko_categories()
        hot = [c for c in cats if c.get("name") not in SECTOR_EXCLUDE][:SECTOR_TOP_N]
        if hot:
            lines.append("【板块热度·24h 市值变化】")
            for c in hot:
                lines.append(f"• {c['name']} {(c['change_24h'] or 0):+.1f}% 市值{_fmt_mcap(c['mcap'])}")

        vals = _value_scan()
        if vals:
            lines.append("")
            lines.append("【价值洼地·P/S 最低（越便宜越靠前）】")
            for ps, name, sector, rev in vals[:5]:
                lines.append(f"• {name} [{sector}] P/S {ps:.0f}x（{ps_tier(ps)}）年化收入${rev/1e6:.0f}M")

        coins = coingecko_radar()
        top = coins[:10]
        if top:
            lines.append("")
            lines.append("【24h 涨幅前列】")
            for c in top:
                lines.append(f"• {c['name']} ({c['symbol'].upper()}) {(c['chg_24h'] or 0):+.1f}% 市值${c['mcap']/1e6:.0f}M")

        troughs = value_trough_signals(coins)
        if troughs:
            lines.append("")
            lines.append("【价格回撤洼地（距ATH深+底部放量）】")
            for c in troughs[:5]:
                lines.append(f"• {c['name']} ({c['symbol'].upper()}) 距ATH {(c['ath_change'] or 0):+.0f}% 换手{c['turnover']:.0f}%")

        rows, stats = _review_signals(coins)
        if rows:
            lines.append("")
            lines.append("【信号复盘（自提醒以来涨跌）】")
            for sym, chg in rows[:10]:
                lines.append(f"• {sym} {chg:+.1f}%" if chg is not None else f"• {sym} 已消失/跌出监测")
            if stats.get("total"):
                lines.append(f"总结: 上涨{stats['up']} / 下跌{stats['down']} / 消失{stats['gone']}")

        listings = binance_listings()
        if listings:
            lines.append("")
            lines.append("【上币公告】")
            for l in listings[:5]:
                lines.append(f"• {l['title']}")

        profiles = dexscreener_new_profiles()
        news = []
        for s in new_token_signals(profiles):
            s["safety"] = goplus_check(s["chain"], s["address"])
            if s["safety"] and s["safety"]["dangerous"]:
                continue
            s["score"] = meme_score(s["safety"], s["liq"], s["vol"], s["chg"], s["age_h"])
            news.append(s)
        news.sort(key=lambda x: x["score"], reverse=True)
        if news:
            lines.append("")
            lines.append("【新币/新池子（含Meme评分）】")
            for s in news[:10]:
                lines.append(f"• {s['symbol']} ({s['chain']}) 流动${s['liq']/1e3:.0f}K +{s['chg']:.0f}% Meme{s['score']}分")

        send_telegram("\n".join(lines))
        print("[digest] 已生成摘要")
    except Exception as e:
        error_alert("digest", str(e))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "digest":
        run_digest()
    else:
        try:
            sys.exit(run_scan())
        except Exception:
            traceback.print_exc()
            error_alert("system", traceback.format_exc(limit=2))
            sys.exit(1)
