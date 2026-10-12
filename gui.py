"""
gui.py
======
Interfaz gráfica del Analizador de Ensayo Charpy, construida con
CustomTkinter. No contiene lógica de cálculo: toda la física vive en
engine.py (MotorCharpy). Este módulo solo se encarga de:

  1. Cargar el archivo .txt exportado por el Arduino (read_arduino_data).
  2. Pedir los parámetros mecánicos del péndulo (masa, radio, momento de
     inercia manual o experimental, pérdida por fricción).
  3. Ejecutar el análisis (MotorCharpy.analizar) y mostrar resultados.
  4. Graficar ángulo, velocidad angular, aceleración angular y energía /
     potencia vs. tiempo, en gráficos ESTÁTICOS (sin toolbar de zoom/pan,
     tal como pide el diseño minimalista del proyecto).

Se importa y se lanza desde main.py; también puede correrse solo
(`python gui.py`) para pruebas rápidas de la interfaz.
"""

import math
import warnings
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Polygon, Rectangle

from engine import (
    ArduinoDataError,
    EnsayoData,
    ResultadoAnalisis,
    ParametrosFisicos,
    MotorCharpy,
    read_arduino_data,
    calcular_inercia_experimental,
)
from exporter import (
    ExportError,
    exportar_reporte_pdf,
    exportar_graficos_jpg,
    exportar_datos_txt,
)

# ============================================================
# PALETA Y ESTILO
# ============================================================
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

COLOR_ACCENT = "#2E86AB"       # azul acero (ángulo / botón principal)
COLOR_VELOCIDAD = "#A23B72"    # magenta
COLOR_ACELERACION = "#F18F01"  # ámbar
COLOR_POTENCIAL = "#3B7A57"    # verde bosque
COLOR_CINETICA = "#C73E1D"     # rojo ladrillo
COLOR_TOTAL = "#444444"        # gris grafito
COLOR_POTENCIA = "#6A4C93"     # violeta
COLOR_CRUDO = "#B0B0B0"        # gris claro (datos crudos, dispersión)
COLOR_AVISO = "#D68910"        # ámbar oscuro (texto de advertencia)

# --- Animación del péndulo ---
FPS_ANIMACION = 30             # cuadros por segundo del temporizador de playback
L_VARILLA = 1.0                # longitud VISUAL de la varilla (unidades del eje)
VELOCIDADES_ANIMACION = {      # factor de cámara lenta (1x = tiempo real del ensayo)
    "0.1x": 0.1,
    "0.25x": 0.25,
    "0.5x": 0.5,
    "1x": 1.0,
}


class CharpyApp(ctk.CTk):
    """Ventana principal del Analizador de Ensayo Charpy."""

    def __init__(self):
        super().__init__()

        self.title("Analizador de Ensayo Charpy")
        self.geometry("1180x700")
        self.minsize(980, 620)

        # --- Estado ---
        self.datos: Optional[EnsayoData] = None
        self.resultado: Optional[ResultadoAnalisis] = None
        self.ultimos_parametros: Optional[ParametrosFisicos] = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._construir_panel_lateral()
        self._construir_panel_graficos()

        # Un 'after' de la animación pendiente no debe disparar contra widgets
        # ya destruidos: corta el temporizador antes de cerrar la ventana.
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ================================================================
    # PANEL LATERAL (carga + parámetros + resultados)
    # ================================================================
    def _construir_panel_lateral(self):
        panel = ctk.CTkScrollableFrame(self, width=300, corner_radius=0)
        panel.grid(row=0, column=0, sticky="nsw")

        ctk.CTkLabel(
            panel, text="Ensayo Charpy", font=ctk.CTkFont(size=21, weight="bold")
        ).pack(anchor="w", padx=18, pady=(18, 0))
        ctk.CTkLabel(
            panel, text="Análisis de energía de impacto",
            font=ctk.CTkFont(size=12), text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=18, pady=(0, 18))

        # --- Carga de archivo ---
        ctk.CTkButton(
            panel, text="Cargar archivo .txt", command=self.cargar_archivo,
            fg_color=COLOR_ACCENT, hover_color="#246a87",
        ).pack(fill="x", padx=18, pady=(0, 4))
        self.lbl_archivo = ctk.CTkLabel(
            panel, text="Sin archivo cargado.", font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"), wraplength=260, justify="left",
        )
        self.lbl_archivo.pack(anchor="w", padx=18, pady=(0, 6))
        self.lbl_metadata = ctk.CTkLabel(
            panel, text="", font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"), wraplength=260, justify="left",
        )
        self.lbl_metadata.pack(anchor="w", padx=18, pady=(0, 16))

        self._separador(panel)

        # --- Parametros mecanicos ---
        ctk.CTkLabel(
            panel, text="Parámetros del péndulo", font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=18, pady=(8, 10))

        self.entry_masa = self._crear_campo(panel, "Masa del péndulo (kg)", valor_inicial="1.0")
        self.entry_radio = self._crear_campo(panel, "Radio eje - centro de masa (m)", valor_inicial="0.30")

        ctk.CTkLabel(
            panel, text="Momento de inercia (I)", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=18, pady=(14, 4))
        ctk.CTkLabel(
            panel, text="Necesario para energía cinética y potencia. Si no se define, "
                        "solo se calcula la energía potencial.",
            font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
            wraplength=260, justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 6))

        self.modo_inercia = ctk.CTkSegmentedButton(
            panel, values=["Manual", "Por período"], command=self._cambiar_modo_inercia
        )
        self.modo_inercia.set("Manual")
        self.modo_inercia.pack(fill="x", padx=18, pady=(0, 8))

        # Sub-frames intercambiables segun el modo elegido
        self.frame_inercia_manual = ctk.CTkFrame(panel, fg_color="transparent")
        self.entry_inercia_manual = self._crear_campo(
            self.frame_inercia_manual, "I (kg·m²) — opcional", placeholder="ej: 0.09"
        )

        self.frame_periodo = ctk.CTkFrame(panel, fg_color="transparent")
        self.entry_periodo = self._crear_campo(
            self.frame_periodo, "Período de oscilación T (s)", placeholder="ej: 1.20"
        )
        ctk.CTkLabel(
            self.frame_periodo,
            text="Medí con cronómetro: soltá el péndulo SIN probeta desde pocos "
                 "grados y dividí el tiempo de N vueltas completas por N.",
            font=ctk.CTkFont(size=10), text_color=("gray45", "gray55"),
            wraplength=260, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        self.lbl_inercia_calculada = ctk.CTkLabel(
            panel, text="", font=ctk.CTkFont(size=11), text_color=COLOR_ACCENT,
            wraplength=260, justify="left",
        )

        self.frame_inercia_manual.pack(fill="x", padx=18, pady=(0, 4))  # estado inicial: Manual

        self._separador(panel)

        # --- Perdida por friccion ---
        ctk.CTkLabel(
            panel, text="Corrección por fricción", font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=18, pady=(8, 6))
        self.entry_friccion = self._crear_campo(
            panel, "Pérdida por fricción (J)", valor_inicial="0.0"
        )

        self._separador(panel)

        # --- Suavizado opcional (Savitzky-Golay) ---
        ctk.CTkLabel(
            panel, text="Suavizado (opcional)", font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=18, pady=(8, 6))
        ctk.CTkLabel(
            panel,
            text="Si la aceleración o la potencia se ven con ruido en \"dientes "
                 "de sierra\", probá una ventana de 9 u 11 (debe ser impar).",
            font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
            wraplength=260, justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 4))
        self.entry_suavizado = self._crear_campo(
            panel, "Ventana Savitzky-Golay (impar)", placeholder="ej: 9 (vacío = sin suavizar)"
        )

        self._separador(panel)

        # --- Calcular ---
        self.btn_calcular = ctk.CTkButton(
            panel, text="Calcular", command=self.calcular, state="disabled",
            fg_color=COLOR_ACCENT, hover_color="#246a87", height=38,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.btn_calcular.pack(fill="x", padx=18, pady=(8, 18))

        self._separador(panel)

        # --- Resultados ---
        ctk.CTkLabel(
            panel, text="Resultados", font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=18, pady=(8, 8))
        self.lbl_resultado = ctk.CTkLabel(
            panel, text="Cargá un archivo y presioná Calcular.",
            font=ctk.CTkFont(size=12), justify="left", wraplength=260, anchor="w",
        )
        self.lbl_resultado.pack(anchor="w", padx=18, pady=(0, 8), fill="x")

        self.lbl_aviso = ctk.CTkLabel(
            panel, text="", font=ctk.CTkFont(size=11), text_color=COLOR_AVISO,
            justify="left", wraplength=260, anchor="w",
        )
        self.lbl_aviso.pack(anchor="w", padx=18, pady=(0, 12), fill="x")

        self.btn_exportar = ctk.CTkButton(
            panel, text="Exportar resultados...", command=self.abrir_dialogo_exportar,
            state="disabled", fg_color="#555555", hover_color="#3d3d3d",
        )
        self.btn_exportar.pack(fill="x", padx=18, pady=(0, 24))

    def _crear_campo(self, parent, texto_label, placeholder="", valor_inicial=None):
        """Crea (label + entry) apilados y devuelve el CTkEntry."""
        ctk.CTkLabel(parent, text=texto_label, font=ctk.CTkFont(size=12)).pack(
            anchor="w", pady=(6, 2)
        )
        entry = ctk.CTkEntry(parent, placeholder_text=placeholder)
        if valor_inicial is not None:
            entry.insert(0, valor_inicial)
        entry.pack(fill="x", pady=(0, 2))
        return entry

    def _separador(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=("gray80", "gray30")).pack(
            fill="x", padx=18, pady=10
        )

    def _cambiar_modo_inercia(self, valor):
        self.lbl_inercia_calculada.pack_forget()
        if valor == "Manual":
            self.frame_periodo.pack_forget()
            self.frame_inercia_manual.pack(fill="x", padx=18, pady=(0, 4))
        else:
            self.frame_inercia_manual.pack_forget()
            self.frame_periodo.pack(fill="x", padx=18, pady=(0, 4))

    # ================================================================
    # PANEL DE GRAFICOS
    # ================================================================
    def _construir_panel_graficos(self):
        self.tabview = ctk.CTkTabview(self, corner_radius=8)
        self.tabview.grid(row=0, column=1, sticky="nsew", padx=(8, 12), pady=12)

        tab_angulo = self.tabview.add("Ángulo")
        tab_vel = self.tabview.add("Velocidad")
        tab_acel = self.tabview.add("Aceleración")
        tab_energia = self.tabview.add("Energía y potencia")
        tab_anim = self.tabview.add("Animación")

        self.fig_angulo, self.ax_angulo, self.canvas_angulo = self._crear_figura(tab_angulo)
        self.fig_vel, self.ax_vel, self.canvas_vel = self._crear_figura(tab_vel)
        self.fig_acel, self.ax_acel, self.canvas_acel = self._crear_figura(tab_acel)

        self.fig_energia = Figure(figsize=(6, 5), dpi=100)
        self.ax_energia = self.fig_energia.add_subplot(211)
        self.ax_potencia = self.fig_energia.add_subplot(212, sharex=self.ax_energia)
        self.fig_energia.tight_layout(pad=3.0)
        self.canvas_energia = FigureCanvasTkAgg(self.fig_energia, master=tab_energia)
        self.canvas_energia.get_tk_widget().pack(fill="both", expand=True)

        self._graficar_placeholder(self.ax_angulo, "ángulo")
        self._graficar_placeholder(self.ax_vel, "velocidad angular")
        self._graficar_placeholder(self.ax_acel, "aceleración angular")
        self.ax_energia.axis("off")
        self.ax_potencia.axis("off")
        self.ax_energia.text(
            0.5, 0.5, "Cargá un archivo y presioná Calcular\npara ver energía y potencia.",
            ha="center", va="center", fontsize=11, color="gray", transform=self.ax_energia.transAxes,
        )
        self.canvas_angulo.draw()
        self.canvas_vel.draw()
        self.canvas_acel.draw()
        self.canvas_energia.draw()

        self._construir_tab_animacion(tab_anim)

        # Pausa el playback cuando se sale de la pestaña de animación. Se
        # configura DESPUÉS de construir la pestaña para que _anim_pausar()
        # siempre encuentre sus widgets. CTkTabview llama al command sin
        # argumentos, por eso se consulta la pestaña actual con .get().
        try:
            self.tabview.configure(command=self._cambiar_pestania)
        except Exception:
            pass  # versiones de CustomTkinter sin callback de pestaña

    def _crear_figura(self, tab_frame):
        fig = Figure(figsize=(6, 5), dpi=100)
        ax = fig.add_subplot(111)
        fig.tight_layout(pad=3.0)
        canvas = FigureCanvasTkAgg(fig, master=tab_frame)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        return fig, ax, canvas

    def _graficar_placeholder(self, ax, nombre_curva):
        ax.clear()
        ax.axis("off")
        ax.text(
            0.5, 0.5, f"Cargá un archivo y presioná Calcular\npara ver {nombre_curva}.",
            ha="center", va="center", fontsize=11, color="gray", transform=ax.transAxes,
        )

    # ================================================================
    # CARGA DE ARCHIVO
    # ================================================================
    def cargar_archivo(self):
        ruta = filedialog.askopenfilename(
            title="Seleccionar archivo de datos del ensayo",
            filetypes=[("Archivos de texto", "*.txt"), ("Todos los archivos", "*.*")],
        )
        if not ruta:
            return

        try:
            datos = read_arduino_data(ruta)
        except ArduinoDataError as e:
            messagebox.showerror("No se pudo leer el archivo", str(e))
            return

        self.datos = datos
        self.resultado = None
        nombre_archivo = ruta.split("/")[-1].split("\\")[-1]
        self.lbl_archivo.configure(
            text=f"Archivo: {nombre_archivo}\n{len(datos.tiempo_s)} muestras "
                 f"({datos.tiempo_s[-1] * 1000:.1f} ms capturados)."
        )

        # Mostrar y aprovechar metadata detectada, si hay
        meta = datos.metadata
        partes_meta = []
        if meta.angulo_inicial_grados is not None:
            partes_meta.append(f"ángulo inicial {meta.angulo_inicial_grados:.2f}°")
        if meta.angulo_final_grados is not None:
            partes_meta.append(f"ángulo de rebote {meta.angulo_final_grados:.2f}°")
        if meta.perdida_friccion_j is not None:
            partes_meta.append(f"fricción calibrada {meta.perdida_friccion_j:.4f} J")
            self.entry_friccion.delete(0, "end")
            self.entry_friccion.insert(0, f"{meta.perdida_friccion_j:.4f}")

        if partes_meta:
            self.lbl_metadata.configure(text="Metadata detectada: " + ", ".join(partes_meta) + ".")
        else:
            self.lbl_metadata.configure(
                text="El archivo solo trae la tabla tiempo/ángulo (sin bloque RESULTADO)."
            )

        self.btn_calcular.configure(state="normal")
        self.btn_exportar.configure(state="disabled")
        self.lbl_resultado.configure(text="Datos cargados. Ajustá los parámetros y presioná Calcular.")
        self.lbl_aviso.configure(text="")

        # Un archivo nuevo invalida la animación del ensayo anterior.
        self._anim_limpiar()

    # ================================================================
    # VALIDACION DE CAMPOS
    # ================================================================
    @staticmethod
    def _parse_float(entry: ctk.CTkEntry, nombre: str, minimo: Optional[float] = None,
                      opcional: bool = False, valor_por_defecto: Optional[float] = None) -> Optional[float]:
        texto = entry.get().strip().replace(",", ".")
        if texto == "":
            if opcional:
                return valor_por_defecto
            raise ValueError(f"'{nombre}' es obligatorio.")
        try:
            valor = float(texto)
        except ValueError:
            raise ValueError(f"'{nombre}' debe ser un número válido (ingresaste: '{texto}').")
        if minimo is not None and valor <= minimo:
            raise ValueError(f"'{nombre}' debe ser mayor que {minimo}.")
        return valor

    # ================================================================
    # CALCULO
    # ================================================================
    @staticmethod
    def _parse_int_opcional(entry: ctk.CTkEntry, nombre: str) -> Optional[int]:
        texto = entry.get().strip()
        if texto == "":
            return None
        try:
            return int(texto)
        except ValueError:
            raise ValueError(f"'{nombre}' debe ser un número entero (ingresaste: '{texto}').")

    def calcular(self):
        if self.datos is None:
            messagebox.showwarning("Sin datos", "Primero cargá un archivo .txt con los datos del ensayo.")
            return

        try:
            masa = self._parse_float(self.entry_masa, "Masa del péndulo", minimo=0)
            radio = self._parse_float(self.entry_radio, "Radio eje-centro de masa", minimo=0)
            friccion = self._parse_float(
                self.entry_friccion, "Pérdida por fricción", opcional=True, valor_por_defecto=0.0
            )
            ventana_suavizado = self._parse_int_opcional(self.entry_suavizado, "Ventana de suavizado")

            if self.modo_inercia.get() == "Manual":
                momento_inercia = self._parse_float(
                    self.entry_inercia_manual, "Momento de inercia",
                    minimo=0, opcional=True, valor_por_defecto=None,
                )
                self.lbl_inercia_calculada.pack_forget()
            else:
                periodo = self._parse_float(self.entry_periodo, "Período de oscilación", minimo=0)
                momento_inercia = calcular_inercia_experimental(periodo, masa, radio)
                self.lbl_inercia_calculada.configure(
                    text=f"I calculado a partir del período: {momento_inercia:.5f} kg·m²"
                )
                self.lbl_inercia_calculada.pack(anchor="w", padx=18, pady=(0, 4))

            parametros = ParametrosFisicos(
                masa_pendulo=masa, radio_centro_masa=radio, momento_inercia=momento_inercia
            )
            motor = MotorCharpy(parametros)

            with warnings.catch_warnings(record=True) as capturadas:
                warnings.simplefilter("always")
                resultado = motor.analizar(
                    self.datos, perdida_friccion_j=friccion, ventana_suavizado=ventana_suavizado
                )

        except (ValueError, ArduinoDataError) as e:
            messagebox.showerror("No se pudo calcular", str(e))
            return

        self.resultado = resultado
        self.ultimos_parametros = parametros
        self.btn_exportar.configure(state="normal")
        self._mostrar_resultado(resultado, capturadas)
        self._graficar(resultado)

    def _mostrar_resultado(self, r: ResultadoAnalisis, avisos):
        texto = (
            f"Ángulo inicial: {r.angulo_inicial_usado_grados:.2f}°\n"
            f"Ángulo de rebote usado: {r.angulo_final_usado_grados:.2f}°\n\n"
            f"Energía absorbida (bruta): {r.energia_absorbida_bruta_j:.4f} J\n"
            f"Energía absorbida (neta):  {r.energia_absorbida_neta_j:.4f} J"
        )
        self.lbl_resultado.configure(text=texto)

        if not r.rebote_confiable or avisos:
            self.lbl_aviso.configure(
                text="⚠ El ángulo de rebote no se pudo confirmar con los datos "
                     "capturados (posible corte del buffer del Arduino a 300 "
                     "muestras). La energía puede estar sobrestimada. Para un "
                     "valor exacto, capturá también la salida del comando 'e' "
                     "del Arduino (bloque RESULTADO) en el mismo archivo."
            )
        else:
            self.lbl_aviso.configure(text="")

    # ================================================================
    # GRAFICOS
    # ================================================================
    def _graficar(self, r: ResultadoAnalisis):
        t_crudo_ms = r.tiempo_crudo_s * 1000.0
        t_ms = r.tiempo_s * 1000.0

        # --- Ángulo ---
        ax = self.ax_angulo
        ax.clear()
        ax.axis("on")
        ax.scatter(t_crudo_ms, r.angulo_crudo_grados, s=10, color=COLOR_CRUDO,
                   alpha=0.6, label="Datos crudos", zorder=2)
        ax.plot(t_ms, r.angulo_suavizado_grados, color=COLOR_ACCENT, linewidth=2,
                label="Spline suavizado", zorder=3)
        ax.axhline(0, color="#999999", linewidth=1, linestyle="--", zorder=1)
        ax.set_xlabel("Tiempo (ms)")
        ax.set_ylabel("Ángulo (°)")
        ax.set_title("Ángulo del péndulo vs. tiempo")
        ax.grid(alpha=0.25)
        ax.legend(loc="best", fontsize=9)
        if not r.rebote_confiable:
            ax.text(0.02, 0.02, "⚠ rebote estimado, no confirmado", transform=ax.transAxes,
                    fontsize=9, color=COLOR_AVISO, va="bottom", ha="left")
        self.canvas_angulo.draw()

        # --- Velocidad angular ---
        ax = self.ax_vel
        ax.clear()
        ax.axis("on")
        ax.plot(t_ms, np.degrees(r.velocidad_angular_rad_s), color=COLOR_VELOCIDAD, linewidth=2)
        ax.axhline(0, color="#999999", linewidth=1, linestyle="--")
        ax.set_xlabel("Tiempo (ms)")
        ax.set_ylabel("Velocidad angular (°/s)")
        ax.set_title("Velocidad angular vs. tiempo (1ra derivada del spline)")
        ax.grid(alpha=0.25)
        self.canvas_vel.draw()

        # --- Aceleración angular ---
        ax = self.ax_acel
        ax.clear()
        ax.axis("on")
        ax.plot(t_ms, np.degrees(r.aceleracion_angular_rad_s2), color=COLOR_ACELERACION, linewidth=2)
        ax.axhline(0, color="#999999", linewidth=1, linestyle="--")
        ax.set_xlabel("Tiempo (ms)")
        ax.set_ylabel("Aceleración angular (°/s²)")
        ax.set_title("Aceleración angular vs. tiempo (2da derivada del spline)")
        ax.grid(alpha=0.25)
        self.canvas_acel.draw()

        # --- Energía y potencia ---
        ax_e = self.ax_energia
        ax_p = self.ax_potencia
        ax_e.clear()
        ax_p.clear()
        ax_e.axis("on")
        ax_p.axis("on")

        ax_e.plot(t_ms, r.energia_potencial_j, color=COLOR_POTENCIAL, linewidth=2, label="Potencial")
        if r.energia_cinetica_j is not None:
            ax_e.plot(t_ms, r.energia_cinetica_j, color=COLOR_CINETICA, linewidth=2, label="Cinética")
            ax_e.plot(t_ms, r.energia_mecanica_total_j, color=COLOR_TOTAL, linewidth=1.5,
                      linestyle="--", label="Total (mecánica)")
        ax_e.set_ylabel("Energía (J)")
        ax_e.set_title("Energía vs. tiempo")
        ax_e.grid(alpha=0.25)
        ax_e.legend(loc="best", fontsize=9)

        if r.potencia_instantanea_w is not None:
            ax_p.plot(t_ms, r.potencia_instantanea_w, color=COLOR_POTENCIA, linewidth=2)
            ax_p.axhline(0, color="#999999", linewidth=1, linestyle="--")
            ax_p.set_ylabel("Potencia (W)")
            ax_p.set_xlabel("Tiempo (ms)")
            ax_p.set_title("Potencia instantánea vs. tiempo")
            ax_p.grid(alpha=0.25)
        else:
            ax_p.axis("off")
            ax_p.text(
                0.5, 0.5, "Definí el momento de inercia (I) para ver\n"
                          "energía cinética, total y potencia instantánea.",
                ha="center", va="center", fontsize=10, color="gray", transform=ax_p.transAxes,
            )

        self.fig_energia.tight_layout(pad=3.0)
        self.canvas_energia.draw()

        # La pestaña de animación se rearma con el mismo spline ya calculado.
        self._preparar_animacion(r)

    # ================================================================
    # ANIMACION DEL PENDULO (pestaña "Animación")
    # ================================================================
    def _construir_tab_animacion(self, tab):
        """Arma la pestaña que reproduce el movimiento del péndulo del ensayo,
        sincronizado con un cursor sobre la curva de ángulo.

        No hay física nueva acá: la pose del péndulo en cada instante sale del
        mismo spline que ya calculó engine.py (angulo_suavizado_grados sobre
        tiempo_s), igual que las curvas de las otras pestañas.
        """
        # --- Barra de controles (abajo del todo) ---
        barra = ctk.CTkFrame(tab, fg_color="transparent")
        barra.pack(side="bottom", fill="x", pady=(8, 0))

        self.btn_anim_play = ctk.CTkButton(
            barra, text="▶  Reproducir", width=130, state="disabled",
            command=self._anim_toggle, fg_color=COLOR_ACCENT, hover_color="#246a87",
        )
        self.btn_anim_play.pack(side="left", padx=(0, 6))

        self.btn_anim_reset = ctk.CTkButton(
            barra, text="↺  Reiniciar", width=110, state="disabled",
            command=self._anim_reiniciar, fg_color="#555555", hover_color="#3d3d3d",
        )
        self.btn_anim_reset.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(barra, text="Velocidad:", font=ctk.CTkFont(size=12)).pack(
            side="left", padx=(0, 6)
        )
        self.opt_anim_velocidad = ctk.CTkOptionMenu(
            barra, values=list(VELOCIDADES_ANIMACION.keys()), width=90,
            command=self._anim_cambiar_velocidad,
        )
        self.opt_anim_velocidad.set("0.25x")
        self.opt_anim_velocidad.pack(side="left")

        self.lbl_anim_info = ctk.CTkLabel(
            barra, text="", font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
        )
        self.lbl_anim_info.pack(side="right")

        # --- Figura: péndulo (izq.) + ángulo vs. tiempo (der.) ---
        self.fig_anim = Figure(figsize=(6, 5), dpi=100)
        self.ax_pend = self.fig_anim.add_subplot(121)
        self.ax_anim_curva = self.fig_anim.add_subplot(122)
        self.fig_anim.tight_layout(pad=3.0)
        self.canvas_anim = FigureCanvasTkAgg(self.fig_anim, master=tab)
        self.canvas_anim.get_tk_widget().pack(fill="both", expand=True)

        # --- Estado del playback ---
        self._anim_t = 0.0                 # instante de dato que se muestra (s)
        self._anim_t_fin = 0.0
        self._anim_t_malla = None          # malla temporal del spline
        self._anim_angulo = None           # ángulo suavizado (grados)
        self._anim_vel_rad = None          # velocidad angular del spline (rad/s)
        self._anim_theta_prev = None       # para detectar el cruce por cero
        self._anim_flash = 0               # cuadros que dura el destello de impacto
        self._anim_playing = False
        self._anim_gen = 0                 # invalida ticks de reproducciones viejas
        self._anim_after_id = None
        self._anim_velocidad = VELOCIDADES_ANIMACION["0.25x"]

        self._anim_placeholder()

    def _anim_placeholder(self):
        """Estado inicial: todavía no hay ningún resultado que animar."""
        self.ax_pend.clear()
        self.ax_pend.axis("off")
        self.ax_pend.text(
            0.5, 0.5,
            "Cargá un archivo y presioná Calcular\npara ver la animación del péndulo.",
            ha="center", va="center", fontsize=11, color="gray",
            transform=self.ax_pend.transAxes,
        )
        self.ax_anim_curva.clear()
        self.ax_anim_curva.axis("off")
        self.canvas_anim.draw()

    def _cambiar_pestania(self):
        """CTkTabview llama a esto SIN argumentos al cambiar de pestaña, así
        que la pestaña activa se consulta con .get()."""
        if self.tabview.get() != "Animación":
            self._anim_pausar()

    # ----------------------------------------------------------------
    # Preparación (una vez por cada Calcular)
    # ----------------------------------------------------------------
    def _preparar_animacion(self, r: ResultadoAnalisis):
        """Deja la pestaña lista para el resultado r y dibuja el cuadro t=0."""
        self._anim_pausar()

        self._anim_t_malla = np.asarray(r.tiempo_s, dtype=float)
        self._anim_angulo = np.asarray(r.angulo_suavizado_grados, dtype=float)
        self._anim_vel_rad = np.asarray(r.velocidad_angular_rad_s, dtype=float)
        self._anim_t = float(self._anim_t_malla[0])
        self._anim_t_fin = float(self._anim_t_malla[-1])
        self._anim_theta_prev = None
        self._anim_flash = 0

        self._dibujar_pendulo_estatico(r)
        self._dibujar_curva_estatica(r)

        self.btn_anim_play.configure(state="normal", text="▶  Reproducir")
        self.btn_anim_reset.configure(state="normal")
        self.fig_anim.tight_layout(pad=3.0)
        self._dibujar_frame_anim()

    def _dibujar_pendulo_estatico(self, r: ResultadoAnalisis):
        """Marco fijo del péndulo: pivote, referencia vertical, posición de
        liberación y probeta. Las piezas móviles se crean acá vacías y se
        actualizan cuadro a cuadro en _dibujar_frame_anim()."""
        ax = self.ax_pend
        ax.clear()
        ax.axis("on")
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(-1.25, 1.25)
        ax.set_ylim(-1.25, 1.25)
        ax.set_xticks([])
        ax.set_yticks([])
        for lado in ("top", "right", "bottom", "left"):
            ax.spines[lado].set_visible(False)

        # Referencia vertical: theta = 0 (punto más bajo, donde va la probeta).
        ax.plot([0, 0], [-1.25, 0.0], color="#CCCCCC", linewidth=1,
                linestyle=":", zorder=1)

        # Posición de liberación (el ángulo inicial que usó el cálculo).
        rad_ini = math.radians(r.angulo_inicial_usado_grados)
        ax.plot([0, L_VARILLA * math.sin(rad_ini)],
                [0, -L_VARILLA * math.cos(rad_ini)],
                color="#CCCCCC", linewidth=1, linestyle="--", zorder=1)

        # Probeta: bloque gris en el punto más bajo.
        self._anim_probeta = Rectangle(
            (-0.17, -1.08), 0.34, 0.13,
            facecolor="#8a8a8a", edgecolor="#5a5a5a", linewidth=1, zorder=2,
        )
        ax.add_patch(self._anim_probeta)

        # Pivote, varilla y martillo (el martillo es un polígono que se reorienta).
        ax.plot([0], [0], marker="o", markersize=6, color=COLOR_TOTAL, zorder=6)
        self._anim_varilla, = ax.plot([], [], color=COLOR_TOTAL, linewidth=3, zorder=5)
        self._anim_martillo = Polygon(
            [(0.0, 0.0)] * 4, closed=True, facecolor=COLOR_ACCENT,
            edgecolor="#1c5670", linewidth=1, zorder=5,
        )
        ax.add_patch(self._anim_martillo)

    def _dibujar_curva_estatica(self, r: ResultadoAnalisis):
        """Curva de ángulo del spline con las líneas de liberación y rebote, más
        el cursor y el punto que se mueven en cada cuadro."""
        ax = self.ax_anim_curva
        ax.clear()
        ax.axis("on")
        t_ms = self._anim_t_malla * 1000.0

        ax.plot(t_ms, self._anim_angulo, color=COLOR_ACCENT, linewidth=2, zorder=3)
        ax.axhline(0, color="#999999", linewidth=1, linestyle="--", zorder=1)
        ax.axhline(r.angulo_inicial_usado_grados, color=COLOR_CRUDO,
                   linewidth=1, linestyle="--", zorder=1)
        ax.axhline(r.angulo_final_usado_grados, color=COLOR_VELOCIDAD,
                   linewidth=1, linestyle="--", zorder=1)
        ax.text(t_ms[0], r.angulo_inicial_usado_grados, " liberación",
                fontsize=8, color="gray", va="bottom")
        ax.text(t_ms[0], r.angulo_final_usado_grados, " rebote",
                fontsize=8, color=COLOR_VELOCIDAD, va="top")

        self._anim_cursor = ax.axvline(t_ms[0], color="#E67E22", linewidth=1.5, zorder=4)
        self._anim_punto, = ax.plot(
            [t_ms[0]], [self._anim_angulo[0]], marker="o", markersize=7,
            color="#E67E22", linestyle="none", zorder=5,
        )

        ax.set_xlabel("Tiempo (ms)")
        ax.set_ylabel("Ángulo (°)")
        ax.set_title("Ángulo vs. tiempo (cursor sincronizado)", fontsize=11)
        ax.grid(alpha=0.25)
        if not r.rebote_confiable:
            ax.text(0.02, 0.02, "⚠ rebote estimado, no confirmado",
                    transform=ax.transAxes, fontsize=9, color=COLOR_AVISO,
                    va="bottom", ha="left")

    # ----------------------------------------------------------------
    # Dibujo cuadro a cuadro
    # ----------------------------------------------------------------
    def _dibujar_frame_anim(self):
        """Ubica el péndulo y el cursor en el instante self._anim_t."""
        if self._anim_t_malla is None:
            return

        t = self._anim_t
        ang = float(np.interp(t, self._anim_t_malla, self._anim_angulo))
        vel_rad = float(np.interp(t, self._anim_t_malla, self._anim_vel_rad))
        rad = math.radians(ang)

        # Varilla: del pivote (0,0) al extremo.
        dx, dy = math.sin(rad), -math.cos(rad)      # dirección de la varilla
        px, py = math.cos(rad), math.sin(rad)       # perpendicular
        x, y = L_VARILLA * dx, L_VARILLA * dy
        self._anim_varilla.set_data([0.0, x], [0.0, y])

        # Martillo: rectángulo perpendicular al extremo de la varilla.
        cx, cy = (L_VARILLA + 0.06) * dx, (L_VARILLA + 0.06) * dy
        largo, espesor = 0.17, 0.075
        self._anim_martillo.set_xy([
            (cx + largo * px + espesor * dx, cy + largo * py + espesor * dy),
            (cx + largo * px - espesor * dx, cy + largo * py - espesor * dy),
            (cx - largo * px - espesor * dx, cy - largo * py - espesor * dy),
            (cx - largo * px + espesor * dx, cy - largo * py + espesor * dy),
        ])

        # Destello de la probeta al cruzar el punto más bajo (impacto).
        if self._anim_theta_prev is not None and self._anim_theta_prev * ang < 0.0:
            self._anim_flash = 6
        self._anim_theta_prev = ang
        if self._anim_flash > 0:
            self._anim_flash -= 1
            self._anim_probeta.set_facecolor(COLOR_ACELERACION)
        else:
            self._anim_probeta.set_facecolor("#8a8a8a")

        # Cursor sincronizado sobre la curva de ángulo.
        t_ms = t * 1000.0
        self._anim_cursor.set_xdata([t_ms, t_ms])
        self._anim_punto.set_data([t_ms], [ang])

        self.ax_pend.set_title(f"t = {t_ms:6.0f} ms     θ = {ang:7.2f}°", fontsize=11)
        self.lbl_anim_info.configure(
            text=f"t = {t_ms:.0f} ms   θ = {ang:.2f}°   ω = {math.degrees(vel_rad):.1f} °/s"
        )
        self.canvas_anim.draw_idle()

    # ----------------------------------------------------------------
    # Controles de reproducción
    # ----------------------------------------------------------------
    def _anim_toggle(self):
        if self._anim_t_malla is None:
            return
        if self._anim_playing:
            self._anim_pausar()
            return

        # Si el playback ya había terminado, vuelve a empezar.
        if self._anim_t >= self._anim_t_fin - 1e-9:
            self._anim_t = float(self._anim_t_malla[0])
            self._anim_theta_prev = None
            self._dibujar_frame_anim()

        self._anim_playing = True
        self._anim_gen += 1
        self.btn_anim_play.configure(text="⏸  Pausar")
        self._anim_tick(self._anim_gen)

    def _anim_pausar(self):
        """Corta el temporizador e invalida cualquier tick pendiente. Es el
        único punto de parada: se usa al pausar, al recalcular, al cambiar de
        pestaña y al cerrar la ventana."""
        self._anim_playing = False
        self._anim_gen += 1
        if self._anim_after_id is not None:
            try:
                self.after_cancel(self._anim_after_id)
            except Exception:
                pass
            self._anim_after_id = None
        try:
            self.btn_anim_play.configure(text="▶  Reproducir")
        except Exception:
            pass  # la ventana ya se está destruyendo

    def _anim_reiniciar(self):
        if self._anim_t_malla is None:
            return
        seguia = self._anim_playing
        self._anim_pausar()
        self._anim_t = float(self._anim_t_malla[0])
        self._anim_theta_prev = None
        self._anim_flash = 0
        self._dibujar_frame_anim()
        if seguia:
            self._anim_toggle()

    def _anim_cambiar_velocidad(self, valor):
        self._anim_velocidad = VELOCIDADES_ANIMACION.get(valor, 0.25)

    def _anim_tick(self, gen):
        """Un cuadro del playback. gen protege contra ticks de animaciones ya
        canceladas (pausa, nuevo cálculo, cambio de pestaña)."""
        if gen != self._anim_gen or not self._anim_playing:
            return

        paso = self._anim_velocidad / FPS_ANIMACION   # segundos de dato por cuadro
        if self._anim_t + paso >= self._anim_t_fin:
            self._anim_t = self._anim_t_fin
            self._dibujar_frame_anim()
            self._anim_pausar()                       # terminó solo
            return

        self._anim_t += paso
        self._dibujar_frame_anim()
        self._anim_after_id = self.after(
            int(1000 / FPS_ANIMACION), lambda: self._anim_tick(gen)
        )

    def _anim_limpiar(self):
        """Vuelve al estado 'sin datos' (al cargar un archivo nuevo)."""
        self._anim_pausar()
        self._anim_t_malla = None
        self._anim_angulo = None
        self._anim_vel_rad = None
        self._anim_placeholder()
        self.btn_anim_play.configure(state="disabled")
        self.btn_anim_reset.configure(state="disabled")

    def destroy(self):
        # Un 'after' pendiente debe morir antes que los widgets.
        self._anim_pausar()
        super().destroy()

    # ================================================================
    # EXPORTACION (delega el trabajo real en exporter.py)
    # ================================================================
    def abrir_dialogo_exportar(self):
        if self.resultado is None:
            messagebox.showwarning("Sin resultados", "Primero hacé un cálculo antes de exportar.")
            return

        dialogo = ctk.CTkToplevel(self)
        dialogo.title("Exportar")
        dialogo.geometry("300x230")
        dialogo.resizable(False, False)
        dialogo.transient(self)
        dialogo.grab_set()

        ctk.CTkLabel(
            dialogo, text="¿Qué querés exportar?", font=ctk.CTkFont(size=14, weight="bold")
        ).pack(pady=(18, 14))

        ctk.CTkButton(
            dialogo, text="Reporte en PDF", fg_color=COLOR_ACCENT, hover_color="#246a87",
            command=lambda: self._exportar_y_cerrar(self._exportar_pdf, dialogo),
        ).pack(fill="x", padx=20, pady=6)
        ctk.CTkButton(
            dialogo, text="Gráficos en JPG", fg_color=COLOR_ACCENT, hover_color="#246a87",
            command=lambda: self._exportar_y_cerrar(self._exportar_jpg, dialogo),
        ).pack(fill="x", padx=20, pady=6)
        ctk.CTkButton(
            dialogo, text="Datos procesados en TXT", fg_color=COLOR_ACCENT, hover_color="#246a87",
            command=lambda: self._exportar_y_cerrar(self._exportar_txt, dialogo),
        ).pack(fill="x", padx=20, pady=6)
        ctk.CTkButton(
            dialogo, text="Cancelar", fg_color="gray50", hover_color="gray35",
            command=dialogo.destroy,
        ).pack(fill="x", padx=20, pady=(16, 6))

    def _exportar_y_cerrar(self, funcion_exportar, dialogo):
        """Ejecuta la exportación elegida y recién después cierra el diálogo
        (si el usuario cancela el file-picker, el diálogo de opciones se
        cierra igual, ya que cancelar no es un error)."""
        dialogo.destroy()
        funcion_exportar()

    def _figuras_actuales(self):
        return {
            "Ángulo vs tiempo": self.fig_angulo,
            "Velocidad angular vs tiempo": self.fig_vel,
            "Aceleración angular vs tiempo": self.fig_acel,
            "Energía y potencia vs tiempo": self.fig_energia,
        }

    def _exportar_pdf(self):
        ruta = filedialog.asksaveasfilename(
            title="Guardar reporte PDF", defaultextension=".pdf",
            filetypes=[("Documento PDF", "*.pdf")],
        )
        if not ruta:
            return
        try:
            exportar_reporte_pdf(
                self.resultado, self.ultimos_parametros, ruta, figuras=self._figuras_actuales()
            )
        except ExportError as e:
            messagebox.showerror("No se pudo exportar el PDF", str(e))
            return
        messagebox.showinfo("Exportado", f"Reporte guardado en:\n{ruta}")

    def _exportar_jpg(self):
        carpeta = filedialog.askdirectory(title="Elegí una carpeta para guardar los gráficos")
        if not carpeta:
            return
        try:
            rutas = exportar_graficos_jpg(self._figuras_actuales(), carpeta, prefijo="charpy")
        except ExportError as e:
            messagebox.showerror("No se pudieron exportar los gráficos", str(e))
            return
        messagebox.showinfo("Exportado", f"Se guardaron {len(rutas)} gráficos en:\n{carpeta}")

    def _exportar_txt(self):
        ruta = filedialog.asksaveasfilename(
            title="Guardar datos procesados", defaultextension=".txt",
            filetypes=[("Archivo de texto", "*.txt")],
        )
        if not ruta:
            return
        try:
            exportar_datos_txt(self.resultado, ruta, parametros=self.ultimos_parametros)
        except ExportError as e:
            messagebox.showerror("No se pudo exportar el archivo", str(e))
            return
        messagebox.showinfo("Exportado", f"Datos guardados en:\n{ruta}")


if __name__ == "__main__":
    # Permite probar la interfaz de forma aislada: python gui.py
    app = CharpyApp()
    app.mainloop()
