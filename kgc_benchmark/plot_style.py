"""Plot colors and sizes shared by report_results.py and scripts/compare_results.py.

The values are the light mode of the dataviz reference palette: the chart surface,
the text inks, the recessive gridline and baseline grays, and the categorical
colors in the palette's validated order. Plots are PNG files at PLOT_DPI, so a
size given in pixels is converted to points with PX.
"""

PLOT_DPI = 150
PX = 72 / PLOT_DPI  # one pixel, in points
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
# One color per series, in the palette's validated order. Lines and stacks validate
# as neighbours; past three series on one plot, where every pair can meet, colors
# alone don't tell the series apart, so such plots add a second encoding.
CATEGORICAL = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#6250d6",  # violet (lightened from #4a3aa7 in the reference palette)
    "#e34948",  # red
]
