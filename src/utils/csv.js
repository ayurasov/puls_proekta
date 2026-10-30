/**
 * CSV для Excel — те же правила, что у выгрузки плана (src/gantt/export.js): разделитель «;», строки через
 * CRLF, UTF-8 с BOM (иначе Excel покажет кириллицу «кракозябрами»), кавычки — только когда нужны.
 */

/** Ячейка CSV: текст, который Excel принял бы за формулу (=, +, -, @), — с апострофом; числа («-3») не трогаем. */
export function csvCell(value) {
  let text = String(value ?? '')
  if (/^[=+\-@\t\r]/.test(text) && !/^-?\d+([.,]\d+)?$/.test(text)) text = `'${text}`
  return /[;"\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/** Файл CSV целиком: заголовок и строки (массивы значений), с BOM в начале. */
export function csvText(header, rows) {
  const lines = [header.map(csvCell).join(';'), ...rows.map((row) => row.map(csvCell).join(';'))]
  return '\uFEFF' + lines.join('\r\n')
}
