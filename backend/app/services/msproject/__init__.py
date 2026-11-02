"""MS Project в «Пульсе» (ADR-0012): чтение MS Project XML, сопоставление с планом, обновление и экспорт.

Чистый Python — без Flask, базы и HTTP; API и интерфейс подключаются снаружи:

* `read_mspdi(файл)` → модель (`model.Project`); ошибки — `MsProjectError` с текстом для сотрудника;
* `map_project(модель, параметры)` → документ загрузки плана (JSON) с предупреждениями и итогами; лаги связей
  подобраны так, что расчёт сроков «Пульса» даёт даты файла (`schedule.py`); ошибки значений — `MsProjectError`;
* `diff_plan(план, документ загрузки, reserved_keys=все ключи плана с корзиной)` → `Changes`;
  `to_ops(изменения, параметры)` → операции плана;
* `write_mspdi(план)` → MS Project XML (bytes);
* `inconsistent_rows(строки, связи, RuCalendar(...))` — строки, которые автосдвиг «Пульса» передвинул бы.
"""

from app.services.msproject.diff import Changes, diff_plan, to_ops
from app.services.msproject.mapping import DEFAULT_OPTIONS, WARNINGS, map_project, normalize_options
from app.services.msproject.model import MsProjectError
from app.services.msproject.reader import read_mspdi
from app.services.msproject.schedule import RuCalendar, inconsistent_rows
from app.services.msproject.writer import write_mspdi

__all__ = [
    "Changes", "DEFAULT_OPTIONS", "MsProjectError", "RuCalendar", "WARNINGS", "diff_plan", "import_document",
    "inconsistent_rows", "map_project", "normalize_options", "read_mspdi", "to_ops", "write_mspdi",
]


def import_document(source, options=None):
    """Файл MS Project XML + параметры загрузки → документ загрузки (чтение и сопоставление за один вызов)."""
    return map_project(read_mspdi(source), options)
