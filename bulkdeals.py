import datetime
import os
import pandas as pd
from curl_cffi import requests
import telebot

# Safely fetch environment variables from GitHub Secrets
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = telebot.TeleBot(BOT_TOKEN) if BOT_TOKEN else None


# ==========================================
# 1. FETCH BULK & BLOCK DEALS FROM NSE
# ==========================================
def fetch_nse_deals():
    """Fetches both Bulk Deals and Block Deals using Chrome TLS impersonation."""
    session = requests.Session(impersonate="chrome120")
    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
    }
    session.headers.update(headers)

    bulk_df, block_df = pd.DataFrame(), pd.DataFrame()
    data_date = datetime.date.today().strftime("%d-%b-%Y")

    try:
        # Step A: Warm up session cookies
        session.get("https://www.nseindia.com", timeout=15)

        # Step B: Fetch Live Snapshot Data
        live_bulk_url = "https://www.nseindia.com/api/snapshot-capital-market-largedeal"
        res = session.get(live_bulk_url, timeout=15)

        if res.status_code == 200:
            json_data = res.json()
            bulk_raw = json_data.get("BULK_DEALS_DATA", []) or json_data.get("data", [])
            block_raw = json_data.get("BLOCK_DEALS_DATA", [])
            
            if bulk_raw:
                bulk_df = pd.DataFrame(bulk_raw)
            if block_raw:
                block_df = pd.DataFrame(block_raw)

        # Step C: Fallback to Historical Archive if live data is empty
        if bulk_df.empty and block_df.empty:
            print("Live data empty. Checking historical archive...")
            for i in range(1, 8):
                target_date = datetime.date.today() - datetime.timedelta(days=i)
                if target_date.weekday() >= 5:
                    continue

                date_str = target_date.strftime("%d-%m-%Y")
                
                # Bulk deals archive
                bulk_url = f"https://www.nseindia.com/api/historical/bulk-deals?from={date_str}&to={date_str}"
                bulk_res = session.get(bulk_url, timeout=15)
                if bulk_res.status_code == 200 and bulk_res.json().get("data"):
                    bulk_df = pd.DataFrame(bulk_res.json()["data"])

                # Block deals archive
                block_url = f"https://www.nseindia.com/api/historical/block-deals?from={date_str}&to={date_str}"
                block_res = session.get(block_url, timeout=15)
                if block_res.status_code == 200 and block_res.json().get("data"):
                    block_df = pd.DataFrame(block_res.json()["data"])

                if not bulk_df.empty or not block_df.empty:
                    data_date = target_date.strftime("%d-%b-%Y")
                    break

        return bulk_df, block_df, data_date
    except Exception as e:
        print(f"Error fetching data from NSE: {e}")
        return pd.DataFrame(), pd.DataFrame(), data_date


def find_column(df, possible_names):
    cols_lower = {col.lower().replace(" ", "_"): col for col in df.columns}
    for name in possible_names:
        if name.lower() in cols_lower:
            return cols_lower[name.lower()]
    return None


# ==========================================
# 2. PROCESS & NET OUT DEALS
# ==========================================
def process_deal_section(df, section_title):
    if df is None or df.empty:
        return f"*{section_title}*\n_No deals reported._\n\n"

    symbol_col = find_column(df, ["symbol", "bd_symbol", "symbol_name", "ticker"])
    trade_col = find_column(df, ["buysell", "buy_sell", "bd_buy_sell", "deal_type", "type"])
    qty_col = find_column(df, ["qty", "quantity_traded", "quantity", "bd_qty_trk", "bd_qty", "traded_quantity"])
    price_col = find_column(df, ["watp", "trade_price_/_wght._avg._price", "trade_price", "tradeprice", "price", "bd_tp_trk", "bd_price"])
    client_col = find_column(df, ["clientname", "client_name", "bd_client_name", "client", "investor_name", "name"])

    if not all([symbol_col, trade_col, qty_col, price_col]):
        return f"*{section_title}*\n❌ _Error parsing dataset structure._\n\n"

    df[trade_col] = df[trade_col].astype(str).str.strip().str.upper()
    df[qty_col] = pd.to_numeric(df[qty_col].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    df[price_col] = pd.to_numeric(df[price_col].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    df["trade_value_cr"] = (df[qty_col] * df[price_col]) / 10_00_00_000

    buy_list = []
    sell_list = []

    grouped = df.groupby(symbol_col)
    for symbol, group in grouped:
        buys = group[group[trade_col] == "BUY"]
        sells = group[group[trade_col] == "SELL"]

        num_buys = len(buys)
        num_sells = len(sells)
        net_deals = num_buys - num_sells

        if net_deals == 0:
            continue

        buy_cr = buys["trade_value_cr"].sum()
        sell_cr = sells["trade_value_cr"].sum()

        buyers_txt = (
            ", ".join([f"{r[client_col]} (₹{r['trade_value_cr']:.2f}Cr)" for _, r in buys.iterrows()])
            if not buys.empty else "None"
        )
        sellers_txt = (
            ", ".join([f"{r[client_col]} (₹{r['trade_value_cr']:.2f}Cr)" for _, r in sells.iterrows()])
            if not sells.empty else "None"
        )

        item = {
            "symbol": symbol,
            "net_deals": net_deals,
            "num_buys": num_buys,
            "num_sells": num_sells,
            "buy_cr": buy_cr,
            "sell_cr": sell_cr,
            "buyers_txt": buyers_txt,
            "sellers_txt": sellers_txt,
        }

        if net_deals > 0:
            buy_list.append(item)
        else:
            sell_list.append(item)

    buy_list = sorted(buy_list, key=lambda x: x["buy_cr"], reverse=True)
    sell_list = sorted(sell_list, key=lambda x: x["sell_cr"], reverse=True)

    output = f"*{section_title}*\n"
    output += "═══════════════════════════\n\n"

    # Net Buy Subsection
    output += f"🟢 *NET BUY STOCKS ({len(buy_list)})*\n"
    if buy_list:
        for row in buy_list:
            output += f"📈 *{row['symbol']}* — (+{row['net_deals']} Net Deals)\n"
            output += f"• Deals: {row['num_buys']} B | {row['num_sells']} S\n"
            output += f"• Buy: ₹{row['buy_cr']:.2f} Cr | Sell: ₹{row['sell_cr']:.2f} Cr\n"
            output += f"• *Buyers*: {row['buyers_txt']}\n"
            if row["sellers_txt"] != "None":
                output += f"• *Sellers*: {row['sellers_txt']}\n"
            output += "\n"
    else:
        output += "_No Net Buy stocks._\n\n"

    # Net Sell Subsection
    output += f"🔴 *NET SELL STOCKS ({len(sell_list)})*\n"
    if sell_list:
        for row in sell_list:
            output += f"📉 *{row['symbol']}* — ({row['net_deals']} Net Deals)\n"
            output += f"• Deals: {row['num_buys']} B | {row['num_sells']} S\n"
            output += f"• Buy: ₹{row['buy_cr']:.2f} Cr | Sell: ₹{row['sell_cr']:.2f} Cr\n"
            if row["buyers_txt"] != "None":
                output += f"• *Buyers*: {row['buyers_txt']}\n"
            output += f"• *Sellers*: {row['sellers_txt']}\n"
            output += "\n"
    else:
        output += "_No Net Sell stocks._\n\n"

    return output


# ==========================================
# 3. GENERATE COMPLETE REPORT
# ==========================================
def generate_report(bulk_df, block_df, data_date):
    header = f"📊 *NSE BULK & BLOCK DEALS ({data_date})*\n"
    header += "───────────────────────────\n\n"

    block_report = process_deal_section(block_df, "📦 BLOCK DEALS")
    bulk_report = process_deal_section(bulk_df, "📊 BULK DEALS")

    return header + block_report + bulk_report


def send_telegram_message(text_report):
    if not bot or not CHAT_ID:
        print(text_report)
        return

    if len(text_report) > 4000:
        chunks = [text_report[i : i + 4000] for i in range(0, len(text_report), 4000)]
        for chunk in chunks:
            bot.send_message(CHAT_ID, chunk, parse_mode="Markdown")
    else:
        bot.send_message(CHAT_ID, text_report, parse_mode="Markdown")


if __name__ == "__main__":
    print("Fetching NSE Bulk & Block Deals...")
    bulk_df, block_df, data_date = fetch_nse_deals()
    report = generate_report(bulk_df, block_df, data_date)
    send_telegram_message(report)
    print("Process completed successfully.")
