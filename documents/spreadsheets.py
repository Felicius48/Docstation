import csv


def read_csv(handle):
    sample = handle.read(4096)
    handle.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = "excel"
    return csv.reader(handle, dialect)


def write_workbook(sheets, destination):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell

    book = Workbook(write_only=True)
    for name, rows in sheets:
        sheet = book.create_sheet(name)
        for row in rows:
            cells = []
            for value in row:
                cell = WriteOnlyCell(sheet, value="" if value is None else str(value))
                cell.data_type = "s"
                cells.append(cell)
            sheet.append(cells)
    if book.worksheets:
        book.save(destination)


def csv_cell(value):
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n"))
        or value.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠"))
    ):
        return "'" + value
    return value


def write_csv(output, rows):
    csv.writer(output, quoting=csv.QUOTE_ALL).writerows([csv_cell(value) for value in row] for row in rows)


def convert_spreadsheet(source, target, output):
    if target == "xlsx":
        with source.open(encoding="utf-8-sig", newline="") as handle:
            write_workbook([("Данные", read_csv(handle))], output)
        return

    from openpyxl import load_workbook

    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            write_csv(handle, workbook.active.values)
    finally:
        workbook.close()
