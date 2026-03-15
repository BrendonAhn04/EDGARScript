import os
import requests
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# SEC requires a descriptive User-Agent. Fetch from environment or use a placeholder.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "YourName/YourProject (youremail@example.com)")
# Global cache to prevent redundant SEC network calls
API_CACHE = {
    "tickers": None,
    "submissions": {},
    "companyfacts": {}
}

COMMON_FINANCIALS = {
    "Net Income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "Revenues": ["Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "Total Assets": ["Assets"],
    "Total Liabilities": ["Liabilities"],
    "Stockholders Equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "Earnings Per Share (Basic)": ["EarningsPerShareBasic"],
    "Cash & Equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "Gross Profit": ["GrossProfit"],
    "Operating Income": ["OperatingIncomeLoss"]
}

def get_xbrl_data(cik, accession_number, data_points, headers, report_date=None):
    try:
        cik_str = str(cik).zfill(10)
        if cik_str in API_CACHE["companyfacts"]:
            facts_json = API_CACHE["companyfacts"][cik_str]
        else:
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json"
            resp = requests.get(url, headers=headers)
            if resp.status_code != 200:
                return {}
            facts_json = resp.json()
            API_CACHE["companyfacts"][cik_str] = facts_json
            
        us_gaap = facts_json.get('facts', {}).get('us-gaap', {})
        
        results = {}

        for label in data_points:
            found_val = "N/A (If pre-2010, data may be in filing text)"
            possible_tags = COMMON_FINANCIALS.get(label, [])
            
            for tag in possible_tags:
                if tag in us_gaap:
                    # Check all units (USD, shares, etc.)
                    for unit_key in us_gaap[tag]['units']:
                        data_list = us_gaap[tag]['units'][unit_key]
                        
                        # Priority 1: Find entry matching specific accession number (Direct Match)
                        # A filing contains multiple years of data. We must find the one matching the Report Date.
                        filing_data = [item for item in data_list if item['accn'] == accession_number]
                        match = None
                        if filing_data:
                            if report_date:
                                match = next((item for item in filing_data if item.get('end') == report_date), None)
                            if not match:
                                # Fallback: use the data with the latest end date in this filing
                                filing_data.sort(key=lambda x: x.get('end', ''), reverse=True)
                                match = filing_data[0]

                        # Priority 2: If no direct match, look for matching Fiscal Year (FY) end date
                        # This helps find historical data tagged in later filings (comparatives)
                        if not match and report_date:
                            matches = [
                                item for item in data_list 
                                if item.get('end') == report_date
                            ]
                            # Filter for Annual data (FY tag OR duration > 360 days)
                            fy_matches = []
                            for m in matches:
                                if m.get('fp') == 'FY':
                                    fy_matches.append(m)
                                elif 'start' in m and 'end' in m:
                                    try:
                                        start_d = datetime.strptime(m['start'], "%Y-%m-%d")
                                        end_d = datetime.strptime(m['end'], "%Y-%m-%d")
                                        if (end_d - start_d).days > 360:
                                            fy_matches.append(m)
                                    except:
                                        pass

                            if fy_matches:
                                # Sort by filing date descending to get the most recent (likely most accurate/restated) value
                                fy_matches.sort(key=lambda x: x.get('filed', ''), reverse=True)
                                match = fy_matches[0]

                        if match:
                            val = match['val']
                            # Simple formatting
                            if abs(val) >= 1_000_000_000:
                                val_str = f"${val/1_000_000_000:.2f}B"
                            elif abs(val) >= 1_000_000:
                                val_str = f"${val/1_000_000:.2f}M"
                            else:
                                val_str = f"${val:,.2f}"
                            found_val = val_str
                            break
                if found_val != "N/A":
                    break
            results[label] = found_val
        
        return results
    except Exception as e:
        return {}

def find_10k_link(ticker_or_name, target_date_str, search_previous=False, data_points=None, date_format="%m/%d/%Y"):
    """
    Finds the 10-K filing URL for a ticker that was filed around a specific date.
    Note: SEC requires a User-Agent string (Company Name and Email).
    """
    try:
        # Convert user input to datetime object
        target_date = datetime.strptime(target_date_str, date_format)
    except ValueError:
        return {"message": "Error: Invalid date for the selected format."}

    try:
        
        # SEC requires a User-Agent string. Be descriptive.
        headers = {"User-Agent": SEC_USER_AGENT}
        if API_CACHE["tickers"] is None:
            try:
                r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=headers)
                r.raise_for_status()
                API_CACHE["tickers"] = r.json()
            except requests.exceptions.RequestException as e:
                return {"message": f"Failed to fetch company tickers: {e}"}
        tickers_json = API_CACHE["tickers"]
        
        search_str = ticker_or_name.strip().upper()
        found_data = None
        
        # Try exact ticker match first
        for item in tickers_json.values():
            if item['ticker'] == search_str:
                found_data = item
                break
        
        # If not found, try partial company name match
        if not found_data:
            for item in tickers_json.values():
                if search_str in item['title'].upper():
                    found_data = item
                    break
        
        cik = found_data['cik_str'] if found_data else None
        
        # 2. Get Submissions for CIK and find the matching 10-K
        if cik:
            cik_str = str(cik).zfill(10)
            if cik_str in API_CACHE["submissions"]:
                subs = API_CACHE["submissions"][cik_str]
            else:
                try:
                    r = requests.get(f"https://data.sec.gov/submissions/CIK{cik_str}.json", headers=headers)
                    r.raise_for_status()
                    subs = r.json()
                    API_CACHE["submissions"][cik_str] = subs
                except requests.exceptions.RequestException as e:
                    return {"message": f"Failed to fetch submissions for CIK {cik_str}: {e}"}
            
            # Start with recent filings
            batches = [subs['filings']['recent']]
            
            # Add older filings if available to search historical data (pre-2015 usually)
            if 'files' in subs['filings']:
                for file_info in subs['filings']['files']:
                    try:
                        older_url = f"https://data.sec.gov/submissions/{file_info['name']}"
                        if older_url in API_CACHE["submissions"]:
                            older_batch = API_CACHE["submissions"][older_url]
                        else:
                            older_batch = requests.get(older_url, headers=headers).json()
                            API_CACHE["submissions"][older_url] = older_batch
                        batches.append(older_batch)
                    except Exception:
                        pass

            best_date = None
            best_filing_info = None

            for batch in batches:
                for i, form in enumerate(batch['form']):
                    if form == '10-K':
                        filing_date = datetime.strptime(batch['filingDate'][i], "%Y-%m-%d")
                        
                        if search_previous:
                            if filing_date <= target_date:
                                if best_date is None or filing_date > best_date:
                                    best_date = filing_date
                                    r_date = batch['reportDate'][i] if 'reportDate' in batch else None
                                    best_filing_info = (batch['accessionNumber'][i], batch['primaryDocument'][i], batch['filingDate'][i], r_date)
                        else:
                            if filing_date >= target_date:
                                if best_date is None or filing_date < best_date:
                                    best_date = filing_date
                                    r_date = batch['reportDate'][i] if 'reportDate' in batch else None
                                    best_filing_info = (batch['accessionNumber'][i], batch['primaryDocument'][i], batch['filingDate'][i], r_date)
            
            if best_filing_info:
                acc, doc, f_date_str, report_date = best_filing_info
                accession = acc.replace("-", "")
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
                
                f_date_formatted = datetime.strptime(f_date_str, "%Y-%m-%d").strftime(date_format)
                message = f"Found 10-K filed on {f_date_formatted}"
                data_results = {}

                if data_points:
                    data_results = get_xbrl_data(cik, acc, data_points, headers, report_date)
                    
                return {"message": message, "url": url, "data": data_results, "filingDate": f_date_str}
            else:
                direction = "on or before" if search_previous else "on or after"
                return {"message": f"No 10-K filing found {direction} {target_date_str}."}
        else:
            return {"message": f"Company/Ticker '{ticker_or_name}' not found."}
    except Exception as e:
        return {"message": f"An error occurred: {e}"}

if __name__ == "__main__":
    # --- Flask Web App ---
    app = Flask(__name__, template_folder='templates')

    @app.route('/')
    def index():
        # Pass financial options to the template for the datalist
        return render_template('index.html', financial_options=list(COMMON_FINANCIALS.keys()))

    @app.route('/api/search', methods=['POST'])
    def api_search():
        data = request.json
        tickers_input = data.get('tickers', '').strip()
        date_inputs = data.get('dates', '').strip()
        search_prev = data.get('searchPrevious', False)
        selected_data = data.get('dataPoints', [])
        
        format_mapping = {
            "MM/DD/YYYY": "%m/%d/%Y",
            "YYYY-MM-DD": "%Y-%m-%d",
            "DD/MM/YYYY": "%d/%m/%Y",
            "MM-DD-YYYY": "%m-%d-%Y"
        }
        date_format_str = format_mapping.get(data.get('dateFormat', 'MM/DD/YYYY'), "%m/%d/%Y")

        if not tickers_input or not date_inputs:
            return jsonify({"error": "Tickers and dates are required."}), 400

        raw_tickers = [t.strip().upper() for t in tickers_input.split(',') if t.strip()]
        raw_dates = list(dict.fromkeys(d.strip() for d in date_inputs.split(',') if d.strip()))

        try:
            # Sort dates from most recent to oldest for processing
            dates = sorted(raw_dates, key=lambda x: datetime.strptime(x, date_format_str), reverse=True)
        except ValueError:
            return jsonify({"error": f"Invalid date found for the format '{data.get('dateFormat')}'."}), 400

        # Pre-resolve tickers/names to avoid duplicate searches (e.g., Apple and AAPL)
        headers = {"User-Agent": SEC_USER_AGENT}
        if API_CACHE["tickers"] is None:
            try:
                API_CACHE["tickers"] = requests.get("https://www.sec.gov/files/company_tickers.json", headers=headers).json()
            except Exception as e:
                 return jsonify({"error": f"Could not fetch company list from SEC: {e}"}), 500
        tickers_json = API_CACHE["tickers"]

        unique_companies = {}  # CIK -> (Display Name, Search Query)
        for t in raw_tickers:
            found_data = None
            # Try exact ticker match first
            for item in tickers_json.values():
                if item['ticker'] == t:
                    found_data = item
                    break
            # If not found, try partial company name match
            if not found_data:
                for item in tickers_json.values():
                    if t in item['title'].upper():
                        found_data = item
                        break
            
            if found_data:
                cik = found_data['cik_str']
                if cik not in unique_companies:
                    unique_companies[cik] = (f"{found_data['title']} ({found_data['ticker']})", found_data['ticker'])
            else:
                # Use the raw query if no match is found
                if t not in unique_companies:
                    unique_companies[t] = (t, t)

        all_results = []
        queries_to_run = list(unique_companies.values())

        for display_name, search_query in queries_to_run:
            company_card = {
                "displayName": display_name,
                "filings": [],
                "notFound": []
            }
            seen_urls = {}
            
            for date_val in dates:
                result = find_10k_link(search_query, date_val, search_prev, selected_data, date_format_str)
                
                url = result.get("url")
                if url:
                    if url in seen_urls:
                        # Add this target date to an existing filing result
                        seen_urls[url]['targetDates'].append(date_val)
                    else:
                        # This is a new filing, store it
                        seen_urls[url] = {
                            'targetDates': [date_val],
                            'message': result['message'],
                            'data': result.get('data', {}),
                            'filingDate': result.get('filingDate'),
                            'url': url
                        }
                else:
                    # No filing found for this date, or an error occurred
                    company_card["notFound"].append({
                        "targetDate": date_val,
                        "message": result.get("message", "An unknown error occurred.")
                    })

            # Sort the unique filings found for this company by filing date (most recent to oldest)
            sorted_filings = sorted(seen_urls.values(), key=lambda item: item.get('filingDate', ''), reverse=True)
            company_card["filings"] = sorted_filings
            all_results.append(company_card)

        return jsonify({"results": all_results})

    # To run the app:
    # 1. Make sure you have Flask installed: pip install Flask
    # 2. Create a folder named 'templates' in the same directory as this script.
    # 3. Inside 'templates', create a file named 'index.html' and paste the HTML content.
    # 4. Run this Python script. It will start a web server.
    # 5. Open your web browser and go to http://127.0.0.1:5000
    app.run(debug=True)