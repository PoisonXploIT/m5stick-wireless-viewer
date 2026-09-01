"""Exporters de datos hacia sistemas externos (Fase 5).

El paquete se importa sin httpx instalado: solo el modulo concreto
(``splunk_hec``) lo necesita, y quien lo cablea (la CLI) hace la comprobacion.
"""

from __future__ import annotations
