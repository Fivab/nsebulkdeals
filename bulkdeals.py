import datetime
import os
import pandas as pd
import requests
import telebot

# Safely fetch environment variables from GitHub Secrets
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = telebot.TeleBot(BOT_TOKEN) if BOT_TOKEN else None


# ==========================================
# 1. FETCH DATA FROM NSE
# ==========================================
def fetch_nse_bulk_deals():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }

    session = requests.Session()
    session.headers.update(headers)

    try:
        session.get("https://www.nseindia.com", timeout=10)
        url = "https://www.nseindia.com/api/snapshot-capital-market-largedeal"
        response = session.get(url, timeout=10)

        if response.status_code == 200:
            json_data = response.json()
            bulk_data = json_data.get("BULK_DEALS_DATA", [])
            if not bulk_data:
                bulk_data = json_data.get("data", [])
            return pd.DataFrame(bulk_data)
        return None
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None


def find_column(df, possible_names):
    cols_lower = {col.lower().replace(" ", "_"): col for col in df.columns}
    for name in possible_names:
        if name.lower() in cols_lower:
            return cols_lower[name.lower()]
    return None


# ==========================================
# 2. PROCESS, CATEGORIZE & SORT DEALS
# ==========================================
def process_and_net_deals(df):
    if df is None or df.empty:
        return "⚠️ *No Bulk Deals data found for today.*"

    symbol_col = find_column(
        df, ["symbol", "bd_symbol", "symbol_name", "ticker"]
    )
    trade_col = find_column(
        df, ["buysell", "buy_sell", "bd_buy_sell", "deal_type", "type"]
    )
    qty_col = find_column(
        df,
        [
            "qty",
            "quantity_traded",
            "quantity",
            "bd_qty_trk",
            "bd_qty",
            "traded_quantity",
        ],
    )
    price_col = find_column(
        df,
        [
            "watp",
            "trade_price_/_wght._avg._price",
            "trade_price",
            "tradeprice",
            "price",
            "bd_tp_trk",
            "bd_price",
        ],
    )
    client_col = find_column(
        df,
        [
            "clientname",
            "client_name",
            "bd_client_name",
            "client",
            "investor_name",
            "name",
        ],
    )

    if not all([symbol_col, trade_col, qty_col, price_col]):
        return "❌ *Error parsing NSE response structure.*"

    # Standardize types and clean formatting
    df[trade_col] = df[trade_col].astype(str).str.strip().str.upper()
    df[qty_col] = pd.to_numeric(
        df[qty_col].astype(str).str.replace(",", ""), errors="coerce"
    ).fillna(0)
    df[price_col] = pd.to_numeric(
        df[price_col].astype(str).str.replace(",", ""), errors="coerce"
    ).fillna(0)

    # Calculate Value in Crores
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

        # Skip balanced deals completely
        if net_deals == 0:
            continue

        buy_cr = buys["trade_value_cr"].sum()
        sell_cr = sells["trade_value_cr"].sum()

        buyers_txt = (
            ", ".join([
                f"{r[client_col]} (₹{r['trade_value_cr']:.2f}Cr)"
                for _, r in buys.iterrows()
            ])
            if not buys.empty
            else "None"
        )
        sellers_txt = (
            ", ".join([
                f"{r[client_col]} (₹{r['trade_value_cr']:.2f}Cr)"
                for _, r in sells.iterrows()
            ])
            if not sells.empty
            else "None"
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

    # Sort Buy stocks by highest Buy Cr, Sell stocks by highest Sell Cr
    buy_list = sorted(buy_list, key=lambda x: x["buy_cr"], reverse=True)
    sell_list = sorted(sell_list, key=lambda x: x["sell_cr"], reverse=True)

    today_str = datetime.date.today().strftime("%d-%b-%Y")
    report = f"📊 *NSE NETTED BULK DEALS ({today_str})*\n"
    report += "───────────────────────────\n\n"

    # Category 1: NET BUY
    report += f"🟢 *1. NET BUY STOCKS ({len(buy_list)})*\n\n"
    if buy_list:
        for row in buy_list:
            report += f"📈 *{row['symbol']}* — (+{row['net_deals']} Net Deals)\n"
            report += (
                f"• Deals: {row['num_buys']} Buys | {row['num_sells']} Sells\n"
            )
            report += f"• Buy Value: ₹{row['buy_cr']:.2f} Cr | Sell Value: ₹{row['sell_cr']:.2f} Cr\n"
            report += f"• *Buyers*: {row['buyers_txt']}\n"
            if row["sellers_txt"] != "None":
                report += f"• *Sellers*: {row['sellers_txt']}\n"
            report += "\n"
    else:
        report += "_No Net Buy stocks today._\n\n"

    report += "───────────────────────────\n\n"

    # Category 2: NET SELL
    report += f"🔴 *2. NET SELL STOCKS ({len(sell_list)})*\n\n"
    if sell_list:
        for row in sell_list:
            report += (
                f"📉 *{row['symbol']}* — ({row['net_deals']} Net Deals)\n"
            )
            report += (
                f"• Deals: {row['num_buys']} Buys | {row['num_sells']} Sells\n"
            )
            report += f"• Buy Value: ₹{row['buy_cr']:.2f} Cr | Sell Value: ₹{row['sell_cr']:.2f} Cr\n"
            if row["buyers_txt"] != "None":
                report += f"• *Buyers*: {row['buyers_txt']}\n"
            report += f"• *Sellers*: {row['sellers_txt']}\n"
            report += "\n"
    else:
        report += "_No Net Sell stocks today._\n"

    return report


# Helper to send message to Telegram
def send_telegram_message(text_report):
    if not bot or not CHAT_ID:
        print(text_report)
        return

    if len(text_report) > 4000:
        chunks = [
            text_report[i : i + 4000] for i in range(0, len(text_report), 4000)
        ]
        for chunk in chunks:
            bot.send_message(CHAT_ID, chunk, parse_mode="Markdown")
    else:
        bot.send_message(CHAT_ID, text_report, parse_mode="Markdown")


# Execute immediately on run
if __name__ == "__main__":
    print("Fetching NSE deals...")
    df = fetch_nse_bulk_deals()
    report = process_and_net_deals(df)
    send_telegram_message(report)
    print("Process finished successfully.")
