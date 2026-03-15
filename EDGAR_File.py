import os
import requests
from datetime import datetime
import webbrowser
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import messagebox

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
        return "Error: Invalid date for the selected format.", None, {}, None

    try:
        
        headers = {"User-Agent": "MyViewer/1.0"}
        if API_CACHE["tickers"] is None:
            API_CACHE["tickers"] = requests.get("https://www.sec.gov/files/company_tickers.json", headers=headers).json()
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
                subs = requests.get(f"https://data.sec.gov/submissions/CIK{cik_str}.json", headers=headers).json()
                API_CACHE["submissions"][cik_str] = subs
            
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
                    
                return message, url, data_results, f_date_str
            else:
                direction = "on or before" if search_previous else "on or after"
                return f"No 10-K filing found {direction} {target_date_str}.", None, {}, None
        else:
            return f"Company/Ticker '{ticker_or_name}' not found.", None, {}, None
    except Exception as e:
        return f"An error occurred: {e}", None, {}, None

if __name__ == "__main__":
    def run_gui():
        root = tk.Tk()
        root.title("SEC EDGAR 10-K Link Finder")
        
        # Center the window
        window_width = 600
        window_height = 700
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        center_x = int(screen_width/2 - window_width/2)
        center_y = int(screen_height/2 - window_height/2)
        root.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')

        # Styling
        style = ttk.Style()
        style.theme_use('clam')  # Cleaner look than default
        
        bg_color = "#f0f2f5"
        root.configure(bg=bg_color)
        
        style.configure("TFrame", background=bg_color)
        style.configure("TLabel", background=bg_color, font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"), foreground="#2c3e50")
        style.configure("SubHeader.TLabel", font=("Segoe UI", 11, "bold"), foreground="#34495e")
        
        # --- Scrollable Setup ---
        main_frame = tk.Frame(root, bg=bg_color)
        main_frame.pack(fill=tk.BOTH, expand=1)

        my_canvas = tk.Canvas(main_frame, bg=bg_color, highlightthickness=0)
        my_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)

        my_scrollbar = tk.Scrollbar(main_frame, orient=tk.VERTICAL, command=my_canvas.yview)
        my_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        my_canvas.configure(yscrollcommand=my_scrollbar.set)
        
        scroll_frame = ttk.Frame(my_canvas)
        
        # Add that New frame to a window in the canvas
        scroll_window = my_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        # Update scrollregion when content changes
        def configure_scroll_region(event):
            my_canvas.configure(scrollregion=my_canvas.bbox("all"))
        
        # Resize the inner frame to match the canvas width
        def configure_window_width(event):
            my_canvas.itemconfig(scroll_window, width=event.width)

        scroll_frame.bind("<Configure>", configure_scroll_region)
        my_canvas.bind("<Configure>", configure_window_width)
        
        def _on_mousewheel(event):
            my_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        my_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        # ------------------------
        
        # Content Padding Frame
        content_frame = ttk.Frame(scroll_frame, padding="20 20 20 20")
        content_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(content_frame, text="SEC EDGAR 10-K Link Finder", style="Header.TLabel").pack(pady=(0, 20))

        # Input Section
        input_frame = ttk.Frame(content_frame)
        input_frame.pack(fill="x", pady=10)

        ttk.Label(input_frame, text="Stock Tickers/Names (comma-separated):", style="SubHeader.TLabel").pack(anchor="w")
        ticker_entry = ttk.Entry(input_frame, font=("Segoe UI", 10))
        ticker_entry.pack(fill="x", pady=(5, 15))

        ttk.Label(input_frame, text="Date Format:", style="SubHeader.TLabel").pack(anchor="w")
        format_var = tk.StringVar(value="MM/DD/YYYY")
        format_combo = ttk.Combobox(input_frame, textvariable=format_var, state="readonly", font=("Segoe UI", 10))
        format_combo['values'] = ("MM/DD/YYYY", "YYYY-MM-DD", "DD/MM/YYYY", "MM-DD-YYYY")
        format_combo.pack(fill="x", pady=(5, 15))

        date_label = ttk.Label(input_frame, text="Target Dates (MM/DD/YYYY, comma-separated):", style="SubHeader.TLabel")
        date_label.pack(anchor="w")
        date_entry = ttk.Entry(input_frame, font=("Segoe UI", 10))
        date_entry.pack(fill="x", pady=(5, 5))
        
        format_combo.bind("<<ComboboxSelected>>", lambda e: date_label.config(text=f"Target Dates ({format_var.get()}, comma-separated):"))

        # Move focus to date entry when pressing Enter in ticker entry
        ticker_entry.bind('<Return>', lambda event: date_entry.focus_set())

        search_prev_var = tk.BooleanVar()
        prev_check = ttk.Checkbutton(input_frame, text="Search for previous filing", variable=search_prev_var)
        prev_check.pack(anchor="w", pady=5)
        
        # Tooltip for checkbox
        def create_tooltip(widget, text):
            def enter(event):
                x = widget.winfo_rootx() + 25
                y = widget.winfo_rooty() + 25
                tw = tk.Toplevel(widget)
                tw.wm_overrideredirect(True)
                tw.wm_geometry(f"+{x}+{y}")
                label = tk.Label(tw, text=text, justify='left',
                               background="#ffffe0", relief='solid', borderwidth=1,
                               font=("Segoe UI", 8))
                label.pack(ipadx=1)
                widget.tooltip_window = tw
            def leave(event):
                tw = getattr(widget, 'tooltip_window', None)
                if tw:
                    tw.destroy()
            widget.bind("<Enter>", enter)
            widget.bind("<Leave>", leave)

        create_tooltip(prev_check, "Checked: Search specifically for filings ON or BEFORE the target date.\nUnchecked: Search for filings ON or AFTER the target date.")

        # --- Data Extraction Section ---
        fin_header = ttk.Label(content_frame, text="Financial Data (Optional):", style="SubHeader.TLabel")
        fin_header.pack(anchor="w", pady=(20, 5))
        
        financial_options = list(COMMON_FINANCIALS.keys())

        # Container for dynamic dropdown rows
        rows_frame = ttk.Frame(content_frame)
        rows_frame.pack(pady=5, fill="x")

        def check_input(event):
            combo = event.widget
            value = combo.get()
            if value == '':
                combo['values'] = financial_options
            else:
                data = [item for item in financial_options if value.lower() in item.lower()]
                combo['values'] = data
            
            # Auto-open dropdown when typing (unless navigation key)
            if event.keysym not in ['Return', 'Escape', 'Up', 'Down']:
                combo.event_generate('<Down>')

        def add_row():
            # Container for the input and its result
            # Detached look: matching background, no border
            row_container = tk.Frame(rows_frame, bg=bg_color, bd=0)
            row_container.pack(fill="x", pady=5)
            
            input_frame = tk.Frame(row_container, bg=bg_color)
            input_frame.pack(fill="x")
            
            combo = ttk.Combobox(input_frame, values=financial_options)
            combo.pack(side=tk.LEFT, expand=True, fill="x", padx=(0, 5))
            combo.bind('<KeyRelease>', check_input)
            
            # X button to remove this specific row container
            x_btn = tk.Button(input_frame, text="✕", command=row_container.destroy, bg="#ffdddd", fg="red", relief="flat", width=3)
            x_btn.pack(side=tk.RIGHT)
            
            # Attach combo and result label to the container for easy access later
            row_container.combo = combo

        # Plus and Clear buttons for data points
        add_btn_frame = ttk.Frame(content_frame)
        add_btn_frame.pack(pady=5)
        ttk.Button(add_btn_frame, text="+ Add Data Point", command=add_row).pack(side=tk.LEFT, padx=5)

        def clear_rows():
            nonlocal rows_frame
            rows_frame.destroy()
            rows_frame = ttk.Frame(content_frame)
            rows_frame.pack(pady=5, fill="x", after=fin_header)
            
            root.update_idletasks()
            my_canvas.configure(scrollregion=my_canvas.bbox("all"))

        ttk.Button(add_btn_frame, text="Clear", command=clear_rows).pack(side=tk.LEFT, padx=5)
        # -------------------------------

        def on_search(event=None):
            tickers_input = ticker_entry.get().strip()
            date_inputs = date_entry.get().strip()
            search_prev = search_prev_var.get()
            
            format_mapping = {
                "MM/DD/YYYY": "%m/%d/%Y",
                "YYYY-MM-DD": "%Y-%m-%d",
                "DD/MM/YYYY": "%d/%m/%Y",
                "MM-DD-YYYY": "%m-%d-%Y"
            }
            date_format_str = format_mapping.get(format_var.get(), "%m/%d/%Y")
            
            selected_data = []
            for row_container in rows_frame.winfo_children():
                if hasattr(row_container, 'combo'):
                    val = row_container.combo.get().strip()
                    if val in financial_options and val not in selected_data:
                        selected_data.append(val)
            
            if not tickers_input or not date_inputs:
                messagebox.showwarning("Input Error", "All inputs are required.")
                return
            
            raw_tickers = [t.strip().upper() for t in tickers_input.split(',') if t.strip()]
            raw_dates = list(dict.fromkeys(d.strip() for d in date_inputs.split(',') if d.strip()))
            
            try:
                dates = sorted(raw_dates, key=lambda x: datetime.strptime(x, date_format_str), reverse=True)
            except ValueError:
                dates = raw_dates
                
            # Clear previous results
            for widget in results_container.winfo_children():
                widget.destroy()
                
            search_btn.config(text="Searching...")
            root.update()
            
            # Pre-resolve tickers/names to avoid duplicates (e.g., Apple and AAPL)
            try:
                headers = {"User-Agent": "MyViewer/1.0"}
                if API_CACHE["tickers"] is None:
                    API_CACHE["tickers"] = requests.get("https://www.sec.gov/files/company_tickers.json", headers=headers).json()
                tickers_json = API_CACHE["tickers"]
            except:
                tickers_json = {}

            unique_companies = {} # CIK -> (Display Name, Search Query)
            for t in raw_tickers:
                found_data = None
                for item in tickers_json.values():
                    if item['ticker'] == t:
                        found_data = item
                        break
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
                    if t not in unique_companies:
                        unique_companies[t] = (t, t)

            queries_to_run = list(unique_companies.values())
            
            for display_name, search_query in queries_to_run:
                # Create one consolidated card per company
                card = tk.Frame(results_container, bg="#ffffff", bd=1, relief="solid")
                card.pack(fill="x", pady=5, padx=5)
                
                tk.Label(card, text=display_name, font=("Segoe UI", 12, "bold"), bg="#ffffff", fg="#2c3e50").pack(anchor="w", padx=10, pady=(10, 5))
                
                seen_urls = {}
                not_found_messages = []
                
                for date_val in dates:
                    message, url, data_results, f_date_str = find_10k_link(search_query, date_val, search_prev, selected_data, date_format_str)
                    
                    if url:
                        if url in seen_urls:
                            seen_urls[url]['dates'].append(date_val)
                        else:
                            seen_urls[url] = {'dates': [date_val], 'message': message, 'data': data_results, 'f_date_str': f_date_str}
                    else:
                        # Not found or error for this date
                        not_found_messages.append((date_val, message))

                # Render not found messages
                for date_val, message in not_found_messages:
                    tk.Label(card, text=f"Target: {date_val} - {message}", font=("Segoe UI", 10), bg="#ffffff", fg="#e74c3c").pack(anchor="w", padx=20, pady=2)

                # Sort the unique filings found for this company by filing date (most recent to oldest)
                sorted_urls = sorted(seen_urls.items(), key=lambda item: item[1]['f_date_str'], reverse=True)

                for url, info in sorted_urls:
                    filing_frame = tk.Frame(card, bg="#f9f9f9", bd=1, relief="flat")
                    filing_frame.pack(fill="x", padx=10, pady=5)
                    
                    target_frame = tk.Frame(filing_frame, bg="#f9f9f9")
                    target_frame.pack(anchor="w", padx=10, pady=(5, 0))
                    
                    tk.Label(target_frame, text="Targets: ", font=("Segoe UI", 10, "bold"), bg="#f9f9f9", fg="#34495e").pack(side="left", padx=0)
                    
                    for idx, d_val in enumerate(info['dates']):
                        color = "#34495e" if idx == 0 else "#d35400"  # Orange for duplicates
                        comma = ", " if idx < len(info['dates']) - 1 else ""
                        tk.Label(target_frame, text=f"{d_val}{comma}", font=("Segoe UI", 10, "bold"), bg="#f9f9f9", fg=color).pack(side="left", padx=0)
                        
                    tk.Label(target_frame, text=f" | {info['message']}", font=("Segoe UI", 10, "bold"), bg="#f9f9f9", fg="#34495e").pack(side="left", padx=0)
                    
                    link_lbl = tk.Label(filing_frame, text=url, fg="#0066cc", cursor="hand2", bg="#f9f9f9", font=("Segoe UI", 9, "underline"))
                    link_lbl.pack(anchor="w", padx=10, pady=(2, 5))
                    link_lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
                    
                    if info['data']:
                        data_frame = tk.Frame(filing_frame, bg="#f9f9f9")
                        data_frame.pack(fill="x", padx=10, pady=(0, 5))
                        
                        for key, val in info['data'].items():
                            color = "red" if "N/A" in val or "Not Found" in val else "green"
                            row = tk.Frame(data_frame, bg="#f9f9f9")
                            row.pack(fill="x", pady=1)
                            tk.Label(row, text=f"{key}:", font=("Segoe UI", 9, "bold"), bg="#f9f9f9").pack(side="left")
                            tk.Label(row, text=f" {val}", font=("Segoe UI", 9), bg="#f9f9f9", fg=color).pack(side="left")
                
                root.update()

            search_btn.config(text="Find Filing")
            
            # Explicitly force canvas scroll region to update after rendering cards
            root.update_idletasks()
            my_canvas.configure(scrollregion=my_canvas.bbox("all"))

        # Trigger search when pressing Enter in date entry
        date_entry.bind('<Return>', on_search)

        # Buttons Section
        btn_frame = ttk.Frame(content_frame)
        btn_frame.pack(pady=20)
        search_btn = ttk.Button(btn_frame, text="Find Filing", command=on_search)
        search_btn.pack(padx=10, ipadx=10, ipady=5)
        
        # Container for all dynamic results
        results_container = tk.Frame(content_frame, bg=bg_color)
        results_container.pack(fill="both", expand=True, pady=(0, 20))

        root.mainloop()

    run_gui()