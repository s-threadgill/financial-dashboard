from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import requests
import re
from datetime import datetime

app = FastAPI()
templates = Jinja2Templates(directory="templates")

SEC_HEADERS = {"User-Agent": "SophieThreadgill sophieathreadgill@gmail.com"}


def get_cik_from_ticker(ticker: str):
    url = "https://www.sec.gov/files/company_tickers.json"
    response = requests.get(url, headers=SEC_HEADERS)
    data = response.json()

    ticker = ticker.upper()
    for company in data.values():
        if company["ticker"] == ticker:
            return str(company["cik_str"]).zfill(10)
    return None


def format_money(val):
    if val is None:
        return "—"
    return "${:,.1f}".format(val / 1_000_000)


def extract_by_frame(concept, pattern=None):
    results = {}
    for v in concept.get("units", {}).get("USD", []):
        frame = v.get("frame", "")
        if pattern:
            if not pattern.match(frame):
                continue
        end = v.get("end")
        results[end] = {
            "val": v["val"],
            "fy": v.get("fy"),
            "fp": v.get("fp"),
            "end": end,
        }
    return results


def calculate_missing_q4(quarters, years):
    # DEBUG
    print("---- Debug Q4 Calculation Start ----")
    print("Original quarters received:")
    for end, data in quarters.items():
        print(
            f"End={end}, FP={data.get('fp')}, Val={data.get('val')}, Year field={data.get('year', 'N/A')}"
        )
    print("Years dict (FY totals) keys:", list(years.keys()))

    fy_groups = {}
    for end, data in quarters.items():
        if data["fp"] in ["Q1", "Q2", "Q3"]:
            end_year = int(data["end"].split("-")[0])
            fy_groups.setdefault(end_year, []).append(data)
    # DEBUG
    print("Quarter groups by calendar year of Q3 end:")
    for year, qlist in fy_groups.items():
        fps = [q["fp"] for q in qlist]
        ends = [q["end"] for q in qlist]
        vals = [q["val"] for q in qlist]
        print(f"Year={year}, FPs={fps}, Ends={ends}, Vals={vals}")

    for end_year, q_list in fy_groups.items():
        # we have only Q1-Q3
        q_map = {q["fp"]: q for q in q_list}
        if all(fp in q_map for fp in ["Q1", "Q2", "Q3"]):
            fy_total_year = end_year + 1
            fy_total = years.get(fy_total_year)
            if fy_total is None:
                print(
                    f" FY total not found for calendar year {fy_total_year}, skipping Q4 calc"
                )
                continue

            total_q1_q3 = sum(q_map[fp]["val"] for fp in ["Q1", "Q2", "Q3"])
            q4_val = fy_total - total_q1_q3
            q4_end = f"{fy_total_year}-09-30"

            quarters[q4_end] = {
                "val": q4_val,
                "fp": "Q4",
                "end": q4_end,
                "year": fy_total_year,
            }
            # DEBUG
            print(f"Calculated Q4 for calendar year {fy_total_year}: {q4_val}")

    return quarters


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, ticker: str):
    cik = get_cik_from_ticker(ticker)
    if not cik:
        return templates.TemplateResponse(
            "index.html", {"request": request, "error": "Ticker not found."}
        )

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    response = requests.get(url, headers=SEC_HEADERS)
    company_facts = response.json()

    us_gaap = company_facts["facts"].get("us-gaap", {})
    revenue = us_gaap.get("RevenueFromContractWithCustomerExcludingAssessedTax", {})
    oper_income = us_gaap.get("OperatingIncomeLoss", {})

    # regex
    q_frame_re = re.compile(r"^CY(\d{4})Q(\d)$")
    y_frame_re = re.compile(r"^CY(\d{4})$")

    rev_q = extract_by_frame(revenue, q_frame_re)
    oi_q = extract_by_frame(oper_income, q_frame_re)

    years_rev = {}
    years_oi = {}
    for v in revenue.get("units", {}).get("USD", []):
        if v.get("fp") == "FY":
            years_rev[v["fy"]] = v["val"]
    for v in oper_income.get("units", {}).get("USD", []):
        if v.get("fp") == "FY":
            years_oi[v["fy"]] = v["val"]

    rev_q = calculate_missing_q4(rev_q, years_rev)
    oi_q = calculate_missing_q4(oi_q, years_oi)

    sorted_q_frames = sorted(rev_q.values(), key=lambda x: x["end"], reverse=True)[:8]

    last_8_quarters = []
    for q in sorted_q_frames:
        quarter_year = q.get("year") or q["end"].split("-")[0]

        last_8_quarters.append(
            {
                "date": (
                    f"Q{q['fp'][-1]} {quarter_year}"
                    if q["fp"].startswith("Q")
                    else q["fp"]
                ),
                "revenue": q["val"],
                "operating_income": oi_q.get(q["end"], {}).get("val"),
                "ebitda": None,
                "yoy_growth": None,
                "margin": None,
            }
        )

    sorted_y_frames = sorted(years_rev.keys(), reverse=True)[:3]
    last_3_years = []
    for fy in sorted_y_frames:
        last_3_years.append(
            {
                "date": str(fy),
                "revenue": years_rev.get(fy),
                "operating_income": years_oi.get(fy),
                "ebitda": None,
                "yoy_growth": None,
                "margin": None,
            }
        )

    company_data = {"quarters": last_8_quarters, "years": last_3_years}

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "ticker": ticker.upper(),
            "data": company_data,
            "format_money": format_money,
        },
    )
