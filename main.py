from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import requests

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


def extract_quarters_and_years(concept):
    quarters = {}
    years = {}

    for v in concept.get("units", {}).get("USD", []):
        val = v.get("val")
        fy = v.get("fy")
        fp = v.get("fp")
        form = v.get("form")
        end = v.get("end")

        if val is None or fy is None or fp is None:
            continue

        if fp in ["Q1", "Q2", "Q3"]:
            quarters[(fy, fp)] = {"val": val, "fy": fy, "fp": fp, "end": end}
        elif fp == "FY":
            years[fy] = val

    # calculate missing Q4
    for fy in years:
        fps_present = [fp for (f, fp) in quarters.keys() if f == fy]
        if "Q4" not in fps_present:
            q1 = quarters.get((fy, "Q1"), {}).get("val")
            q2 = quarters.get((fy, "Q2"), {}).get("val")
            q3 = quarters.get((fy, "Q3"), {}).get("val")
            if q1 is not None and q2 is not None and q3 is not None:
                q4_val = years[fy] - (q1 + q2 + q3)
                fy_end = None
                for v in concept.get("units", {}).get("USD", []):
                    if v.get("fy") == fy and v.get("fp") == "FY":
                        fy_end = v.get("end")
                        break
                quarters[(fy, "Q4")] = {
                    "val": q4_val,
                    "fy": fy,
                    "fp": "Q4",
                    "end": fy_end,
                }

    return quarters, years


def get_last_n_quarters(quarters_dict, n=8):
    quarter_order = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    sorted_quarters = sorted(
        quarters_dict.values(),
        key=lambda x: (x["fy"], quarter_order[x["fp"]]),
        reverse=True,
    )
    return sorted_quarters[:n]


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

    rev_quarters, rev_years = extract_quarters_and_years(revenue)
    oi_quarters, oi_years = extract_quarters_and_years(oper_income)

    last_8_quarters = get_last_n_quarters(rev_quarters, n=8)
    last_3_years = sorted(rev_years.keys(), reverse=True)[:3]

    quarters_data = []
    for q in last_8_quarters:
        fy = q["fy"]
        fp = q["fp"]
        quarters_data.append(
            {
                "date": f"{fp} {fy}",
                "revenue": q["val"],
                "operating_income": oi_quarters.get((fy, fp), {}).get("val"),
                "ebitda": None,
                "yoy_growth": None,
                "margin": None,
            }
        )

    years_data = []
    for fy in last_3_years:
        years_data.append(
            {
                "date": str(fy),
                "revenue": rev_years.get(fy),
                "operating_income": oi_years.get(fy),
                "ebitda": None,
                "yoy_growth": None,
                "margin": None,
            }
        )

    company_data = {"quarters": quarters_data, "years": years_data}

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "ticker": ticker.upper(),
            "data": company_data,
            "format_money": format_money,
        },
    )
