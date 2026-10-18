"""The proof behind every gap: the list of students (or months) that make it up. Used by the portal (CSV downloads), by the Excel evidence
workbook, and by gap_report.py (to pick one real student as the example for each gap).

Every GEW-vs-Iteration gap is defined once here: which students belong to it (a WHERE on the comparison table cmp_student) and which
columns show the proof. The effect on revenue of each gap comes from cmp_effect_monthly (see gaps.py).
"""
from __future__ import annotations

import io

import config

BR = ("branch", "REPLACE(b.branch_name, ' Government', '')", "branch")
REG = ("reg", "s.reg_no", "student_no")

GAPS = [
    dict(id="G-I1", eff=("os_notin", "os_notin_fp"), where="s.kind = 'only_sheet' AND s.reason LIKE 'not in the GEW%'", order="ABS(s.rev_sheet) DESC",
         cols=[REG, BR, ("i_start", "i.eff_start", "iteration_start"), ("i_end", "i.eff_end", "iteration_end"),
               ("i_fp", "i.payable_ex_gst", "iteration_final_total_payable_ex_gst"), ("i_k", "i.adj_collection", "iteration_total_adjusted_collection"),
               ("i_status", "i.status", "iteration_status"), ("g_n", "COALESCE(p.n, 0)", "gew_payments_count"),
               ("g_t", "COALESCE(p.t, 0)", "gew_payments_total_with_gst"), ("eff", "-s.rev_sheet", "revenue_effect_this_year_gew_minus_iteration")]),
    dict(id="G-I2", eff=("os_ended", "os_ended_fp"), where="s.kind = 'only_sheet' AND s.reason LIKE 'in the GEW data, but its batch ended%'", order="ABS(s.rev_sheet) DESC",
         cols=[REG, BR, ("g_adm", "e.joining_date", "gew_admission_date"), ("g_bs", "rb.batch_start_date", "gew_batch_start_date"), ("g_end", "e.batch_end", "gew_batch_end_date"),
               ("g_book", "e.total_payable", "gew_booking_with_gst"), ("i_start", "i.eff_start", "iteration_start"), ("i_end", "i.eff_end", "iteration_end"),
               ("i_fp", "i.payable_ex_gst", "iteration_final_total_payable_ex_gst"), ("i_k", "i.adj_collection", "iteration_total_adjusted_collection"),
               ("eff", "-s.rev_sheet", "revenue_effect_this_year_gew_minus_iteration")]),
    dict(id="G-I2b", eff=("os_later", "os_later_fp"), where="s.kind = 'only_sheet' AND s.reason LIKE 'in the GEW data, but its start date is after%'", order="ABS(s.rev_sheet) DESC",
         cols=[REG, BR, ("g_adm", "e.joining_date", "gew_admission_date"), ("g_end", "e.batch_end", "gew_batch_end_date"), ("g_book", "e.total_payable", "gew_booking_with_gst"),
               ("i_start", "i.eff_start", "iteration_start"), ("i_end", "i.eff_end", "iteration_end"), ("i_k", "i.adj_collection", "iteration_total_adjusted_collection"),
               ("eff", "-s.rev_sheet", "revenue_effect_this_year_gew_minus_iteration")]),
    dict(id="G-I3", eff=("oo_missing", "oo_missing"), where="s.kind = 'only_ours' AND s.reason LIKE 'admitted before%'", order="s.rev_ours DESC",
         cols=[REG, BR, ("g_adm", "s.start_ours", "gew_admission_date"), ("g_end", "s.end_ours", "gew_batch_end_date"), ("g_book", "e.total_payable", "gew_booking_with_gst"),
               ("g_status", "e.status", "gew_scheme_status"), ("eff", "s.rev_ours", "gew_revenue_this_year")]),
    dict(id="G-I3b", eff=("oo_zero", "oo_zero"), where="s.kind = 'only_ours' AND s.reason LIKE 'the booking amount is zero%'", order="s.reg_no",
         cols=[REG, BR, ("g_adm", "s.start_ours", "gew_admission_date"), ("g_end", "s.end_ours", "gew_batch_end_date"), ("g_book", "e.total_payable", "gew_booking_with_gst"),
               ("eff", "s.rev_ours", "gew_revenue_this_year")]),
    dict(id="G-I4", eff=("oo_after", "oo_after"), where="s.kind = 'only_ours' AND s.reason LIKE 'admitted after%'", order="s.rev_ours DESC",
         cols=[REG, BR, ("g_adm", "s.start_ours", "gew_admission_date"), ("g_end", "s.end_ours", "gew_batch_end_date"), ("g_book", "e.total_payable", "gew_booking_with_gst"),
               ("eff", "s.rev_ours", "gew_revenue_this_year")]),
    dict(id="G-I5", eff=("date_start", "date_start"), where="s.kind = 'both' AND s.start_month_differs = 1", order="ABS(s.eff_date) DESC",
         cols=[REG, BR, ("g_adm", "s.start_ours", "gew_admission_date"), ("g_bs", "rb.batch_start_date", "gew_batch_start_date"), ("i_start", "s.start_sheet", "iteration_start"),
               ("days", "s.start_diff_days", "days_gew_start_minus_iteration_start"), ("g_end", "s.end_ours", "gew_batch_end_date"), ("i_end", "s.end_sheet", "iteration_end"),
               ("eff", "s.eff_date", "revenue_effect_of_start_and_end_dates_together")]),
    dict(id="G-I6", eff=("date_end", "date_end"), where="s.kind = 'both' AND s.end_month_differs = 1", order="ABS(s.eff_date) DESC",
         cols=[REG, BR, ("g_end", "s.end_ours", "gew_batch_end_date"), ("i_end", "s.end_sheet", "iteration_end"), ("days", "s.end_diff_days", "days_gew_end_minus_iteration_end"),
               ("g_adm", "s.start_ours", "gew_admission_date"), ("i_start", "s.start_sheet", "iteration_start"),
               ("eff", "s.eff_date", "revenue_effect_of_start_and_end_dates_together")]),
    dict(id="G-I7", eff=("amount_def", None), where="s.kind = 'both' AND ABS(s.amt_ours_all - s.amt_sheet) > 5", order="ABS(s.eff_amount) DESC",
         cols=[REG, BR, ("g_book", "ROUND(s.amt_ours_all, 2)", "gew_booking_ex_gst"), ("i_k", "s.amt_sheet", "iteration_total_adjusted_collection"),
               ("diff", "ROUND(s.amt_ours_all - s.amt_sheet, 2)", "gew_minus_iteration"),
               ("g_col", "ROUND(COALESCE(pc.t, 0) / :gst, 2)", "gew_collected_up_to_iteration_date_ex_gst"), ("eff", "s.eff_amount", "revenue_effect_this_year")]),
    dict(id="G-I8", eff=(None, "amount_def_fp"), where="s.kind = 'both' AND ABS(s.amt_ours_all - i.payable_ex_gst) > 5", order="ABS(s.amt_ours_all - i.payable_ex_gst) DESC",
         cols=[REG, BR, ("g_book", "ROUND(s.amt_ours_all, 2)", "gew_booking_ex_gst"), ("i_fp", "i.payable_ex_gst", "iteration_final_total_payable_ex_gst"),
               ("diff", "ROUND(s.amt_ours_all - i.payable_ex_gst, 2)", "gew_minus_iteration"), ("i_bill", "i.bill_amount", "iteration_bill_amount_with_gst"),
               ("g_book_gst", "e.total_payable", "gew_booking_with_gst"), ("g_status", "e.status", "gew_scheme_status")]),
]
BY_ID = {g["id"]: g for g in GAPS}


def _rb(conn) -> str:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    parts = [f"""SELECT CAST(regno AS INTEGER) reg_no, batch_start_date FROM {t} WHERE last_seen_on = (SELECT MAX(last_seen_on) FROM {t})"""
             for t in ("raw_gew_enroll_dump_fy26", "raw_gew_enroll_dump_fy27") if t in have]
    return " UNION ALL ".join(parts) if parts else "SELECT NULL reg_no, NULL batch_start_date WHERE 0"


def _sql(conn, gap, select, limit=None, order=True):
    return f"""WITH rb AS ({_rb(conn)})
        SELECT {select}
        FROM cmp_student s JOIN dim_branch b ON b.branch_id = s.branch_id
        LEFT JOIN ref_iter_student i ON i.reg_no = s.reg_no
        LEFT JOIN fact_enrollment e ON e.reg_no = s.reg_no
        LEFT JOIN (SELECT reg_no, COUNT(*) n, SUM(net_paid_amount) t FROM fact_collection GROUP BY reg_no) p ON p.reg_no = s.reg_no
        LEFT JOIN (SELECT reg_no, SUM(net_paid_amount) t FROM fact_collection WHERE receipt_date <= :cut GROUP BY reg_no) pc ON pc.reg_no = s.reg_no
        LEFT JOIN rb ON rb.reg_no = s.reg_no
        WHERE {gap['where']}""" + (f" ORDER BY {gap['order']}" if order else "") + (f" LIMIT {int(limit)}" if limit else "")


def _params(conn):
    cut = conn.execute("SELECT value FROM meta WHERE key='iter_asof'").fetchone()
    return {"cut": cut[0] if cut else "9999-12-31", "gst": config.GST_DIVISOR}


def count(conn, gid: str) -> int:
    g = BY_ID[gid]
    return conn.execute(_sql(conn, g, "COUNT(*)", order=False), _params(conn)).fetchone()[0]


def evidence(conn, gid: str, limit=None):
    """(header names, rows) for one gap: every student that belongs to it, biggest effect first."""
    g = BY_ID[gid]
    rows = conn.execute(_sql(conn, g, ", ".join(c[1] for c in g["cols"]), limit), _params(conn)).fetchall()
    return [c[2] for c in g["cols"]], rows


def typical(conn, gid: str):
    """One real student from the upper quarter of the list (not the biggest one-off, not a tiny case): a dict keyed by the short column names."""
    g = BY_ID[gid]
    rows = conn.execute(_sql(conn, g, ", ".join(c[1] for c in g["cols"])), _params(conn)).fetchall()
    if not rows:
        return None
    return dict(zip([c[0] for c in g["cols"]], rows[len(rows) // 4]))


def flag_rows(conn, flag_id: str):
    rows = conn.execute("""SELECT f.reg_no, REPLACE(b.branch_name, ' Government', ''), f.detail, f.amount, f.date1, f.date2
                           FROM dq_flag_row f LEFT JOIN dim_branch b ON b.branch_id = f.branch_id WHERE f.flag_id = ?
                           ORDER BY ABS(COALESCE(f.amount, 0)) DESC, f.reg_no""", (flag_id,)).fetchall()
    return ["student_no", "branch", "what_is_wrong", "amount", "date_1", "date_2"], rows


def build_workbook(conn) -> bytes:
    """One Excel file with the register and the list of cases behind every gap and every flag (to attach to a mail)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2F4B7C")

    def sheet(name, header, rows, widths=None, wrap=()):
        ws = wb.create_sheet(name[:31])
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill, c.alignment = head, fill, Alignment(wrap_text=True, vertical="center")
        for r in rows:
            ws.append(list(r))
        ws.freeze_panes = "A2"
        for i, h in enumerate(header, 1):
            w = (widths or {}).get(i - 1) or min(max(len(str(h)) + 2, 12), 34)
            ws.column_dimensions[get_column_letter(i)].width = w
        for col in wrap:
            for row in ws.iter_rows(min_row=2, min_col=col + 1, max_col=col + 1):
                for c in row:
                    c.alignment = Alignment(wrap_text=True, vertical="top")
        return ws

    meta = dict(conn.execute("SELECT key, value FROM meta"))
    ws = wb.active
    ws.title = "Read me"
    for line in ("GEW gap report: proof for every gap", "",
                 f"GEW data till: {meta.get('snapshot_date')}", f"Iteration sheet prepared on: {meta.get('iter_asof')}", f"Built on: {meta.get('built_at')}", "",
                 "GEW data = our enrollment and collection sheets. Revenue is counted on the booking (total fee payable without GST), shared equally over the months from admission to batch end.",
                 "Iteration data = the 'Govt Offline Iteration' sheet made by the P&L team. It is shown on two amounts: Total Adjusted Collection (K) and Final Total Payable.",
                 "Finance P&L = the monthly P&L workbook, line 'Revenue'.", "",
                 "Sheet 'Gap register' lists every gap. Sheets G-I1 ... G-I8 list every student behind each GEW-vs-Iteration gap. Sheet 'Finance months' has the month-by-month numbers.",
                 "Sheets 'F-...' list the students or payments behind each data flag in the GEW data."):
        ws.append([line])
    ws.column_dimensions["A"].width = 140
    ws["A1"].font = Font(bold=True, size=14)

    reg = conn.execute("""SELECT gap_id, grp, title, plain, n, unit, eff_fy_k, eff_win_k, eff_fy_fp, eff_win_fp, window, owner, proof_reg, proof_text
                          FROM gap_register ORDER BY sort""").fetchall()
    sheet("Gap register", ["id", "between", "what is different", "what it means", "count", "unit", "effect this year (Iteration on Total Adjusted Collection), Rs",
                           "effect in the Iteration months (same basis), Rs", "effect this year (Iteration on Final Total Payable), Rs", "effect in the Iteration months (same basis), Rs",
                           "months", "who can close it", "example student", "proof (the example, in words)"], reg,
          widths={2: 46, 3: 70, 13: 90}, wrap=(2, 3, 13))
    fin = conn.execute("SELECT gap_id, title, values_json FROM gap_register WHERE grp = 'Finance P&L' ORDER BY sort").fetchall()
    import json
    rows = []
    for gid, title, vj in fin:
        v = json.loads(vj)
        for i, m in enumerate(v["months"]):
            rows.append((gid, title, m, v["unit"], v["gew"][i], v["it_k"][i], v["it_fp"][i], v["fin"][i]))
    sheet("Finance months", ["id", "what", "month", "unit", "GEW data", "Iteration (Total Adjusted Collection)", "Iteration (Final Total Payable)", "Finance P&L"], rows, widths={1: 50})
    for g in GAPS:
        if conn.execute("SELECT 1 FROM gap_register WHERE gap_id = ?", (g["id"],)).fetchone():
            h, r = evidence(conn, g["id"])
            sheet(g["id"], h, r)
    for (fid,) in conn.execute("SELECT DISTINCT flag_id FROM dq_flag_row ORDER BY flag_id"):
        h, r = flag_rows(conn, fid)
        sheet("F-" + fid, h, r, widths={2: 70})
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
