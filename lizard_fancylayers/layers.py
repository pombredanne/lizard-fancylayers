import datetime
import logging
import mapnik
import math
import os
import pytz

from django.conf import settings
from django.http import Http404

from lizard_map import coordinates
from lizard_map import workspace
from lizard_map.adapter import Graph, FlotGraph
from lizard_map.mapnik_helper import add_datasource_point
from lizard_map.models import ICON_ORIGINALS
from lizard_map.symbol_manager import SymbolManager

from lizard_datasource import properties
from lizard_datasource import datasource

logger = logging.getLogger(__name__)


def html_to_mapnik(color):
    r, g, b = color[0:2], color[2:4], color[4:6]
    rr, gg, bb = int(r, 16), int(g, 16), int(b, 16)

    return rr / 255.0, gg / 255.0, bb / 255.0, 1.0


def symbol_filename(color):
    symbol_manager = SymbolManager(
        ICON_ORIGINALS,
        os.path.join(settings.MEDIA_ROOT, 'generated_icons'))

    output_filename = symbol_manager.get_symbol_transformed(
        'meetpuntPeil.png', mask=('meetpuntPeil_mask.png',),
        color=color)
    output_filename_abs = os.path.join(
        settings.MEDIA_ROOT, 'generated_icons', output_filename)
    return output_filename_abs


class FancyLayersAdapter(workspace.WorkspaceItemAdapter):
    """Registered as adapter_fancylayers."""

    def __init__(self, *args, **kwargs):
        super(FancyLayersAdapter, self).__init__(*args, **kwargs)

        self.choices_made = datasource.ChoicesMade(
            json=self.layer_arguments['choices_made'])
        self.datasource = datasource.datasource(
            choices_made=self.choices_made)

    def layer(self, layer_ids=None, webcolor=None, request=None):
        logger.debug("In lizard_fancylayers.layer")
        # We only do point layers right now
        if not self.datasource.has_property(properties.LAYER_POINTS):
            logger.debug("Datasource is not a point layer.")
            return [], {}

        layers = []
        styles = {}

        locations = list(self.datasource.locations())
        colors = {"default": html_to_mapnik('0000ff')}
        logger.debug("1")
        for location in locations:
            if location.color is not None:
                colors[location.color] = html_to_mapnik(location.color)

        style = mapnik.Style()
        logger.debug("2")

        for colorname, color in colors.iteritems():
            rule = mapnik.Rule()
            symbol = mapnik.PointSymbolizer(
                symbol_filename(color), 'png', 16, 16)
            symbol.allow_overlap = True
            rule.symbols.append(symbol)
            rule.filter = mapnik.Filter("[Color] = '{0}'".format(colorname))
            style.rules.append(rule)

        styles['trivialStyle'] = style
        logger.debug("3")

        layer = mapnik.Layer("Fancy Layers layer", coordinates.WGS84)
        layer.datasource = mapnik.PointDatasource()
        layer.styles.append('trivialStyle')
        logger.debug("4 - {0}".format(locations))

        for location in locations:
            color = location.color or 'default'
            logger.debug('{0}: {1}'.format(location, color))
            add_datasource_point(
                layer.datasource, location.longitude,
                location.latitude,
                'Color', str(color))
        logger.debug("5")

        layers.append(layer)
        return layers, styles

    def search(self, google_x, google_y, radius=None):
        """Return list of dict {'distance': <float>, 'timeserie':
        <timeserie>} of closest fews point that matches x, y, radius.
        """
        def distance(x1, y1, x2, y2):
            return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

        locations = self.datasource.locations()

        result = []
        for location in locations:
            x, y = coordinates.wgs84_to_google(
                location.longitude,
                location.latitude)
            dist = distance(google_x, google_y, x, y)

            if dist < radius:
                result.append(
                    {'distance': dist,
                     'name': location.description(),
                     'shortname': location.identifier,
                     'workspace_item': self.workspace_item,
                     'identifier': {'identifier': location.identifier},
                     'google_coords': (x, y),
                     'object': None})
        result.sort(key=lambda item: item['distance'])
        return result[:3]  # Max 3.

    def html(self, identifiers=None, layout_options=None):
        return self.html_default(
            identifiers=identifiers,
            layout_options=layout_options)

    def location(self, identifier, layout=None):
        locations = self.datasource.locations()
        for location in locations:
            if location.identifier == identifier:
                break
        else:
            return None

        google_x, google_y = coordinates.wgs84_to_google(
            location.longitude, location.latitude)

        identifier_to_return = {
            'identifier': identifier
            }
        if layout is not None:
            identifier_to_return['layout'] = layout

        description = location.description()

        return {
            'google_coords': (google_x, google_y),
            'name': description,
            'shortname': description,
            'workspace_item': self.workspace_item,
            'identifier': identifier_to_return,
            'object': location
            }

    def image(
        self, identifiers, start_date, end_date, width=380.0, height=250.0,
        layout_extra=None, raise_404_if_empty=False):
        # Initial version taken from lizard-fewsjdbc

        return self._render_graph(
            identifiers, start_date, end_date, width=width, height=height,
            layout_extra=layout_extra, raise_404_if_empty=raise_404_if_empty,
            GraphClass=Graph)

    def flot_graph_data(
        self, identifiers, start_date, end_date, layout_extra=None,
        raise_404_if_empty=False
    ):
        return self._render_graph(
            identifiers, start_date, end_date, layout_extra=layout_extra,
            raise_404_if_empty=raise_404_if_empty,
            GraphClass=FlotGraph)

    def _render_graph(
        self, identifiers, start_date, end_date, layout_extra=None,
        raise_404_if_empty=False, GraphClass=Graph, **extra_params):
        """
        Visualize timeseries in a graph.

        Legend is always drawn.

        New: this is now a more generalized version of image(), to
        support FlotGraph.
        """

        logger.debug("_RENDER_GRAPH entered")
        logger.debug("identifiers: {0}".format(identifiers))

        def apply_lines(identifier, values, location_name):
            """Adds lines that are defined in layout. Uses function
            variable graph, line_styles.

            Inspired by fewsunblobbed"""

            layout = identifier['layout']

            if "line_min" in layout:
                graph.axes.axhline(
                    min(values),
                    color=line_styles[str(identifier)]['color'],
                    lw=line_styles[str(identifier)]['min_linewidth'],
                    ls=line_styles[str(identifier)]['min_linestyle'],
                    label='Minimum %s' % location_name)
            if "line_max" in layout:
                graph.axes.axhline(
                    max(values),
                    color=line_styles[str(identifier)]['color'],
                    lw=line_styles[str(identifier)]['max_linewidth'],
                    ls=line_styles[str(identifier)]['max_linestyle'],
                    label='Maximum %s' % location_name)
            if "line_avg" in layout and values:
                average = sum(values) / len(values)
                graph.axes.axhline(
                    average,
                    color=line_styles[str(identifier)]['color'],
                    lw=line_styles[str(identifier)]['avg_linewidth'],
                    ls=line_styles[str(identifier)]['avg_linestyle'],
                    label='Gemiddelde %s' % location_name)

        line_styles = self.line_styles(identifiers)

        locations = list(self.datasource.locations())
        today = datetime.datetime.now()

        graph = GraphClass(start_date, end_date, today=today,
                      tz=pytz.timezone(settings.TIME_ZONE), **extra_params)
        graph.axes.grid(True)
#        parameter_name, unit = self.jdbc_source.get_name_and_unit(
#            self.parameterkey)
#        graph.axes.set_ylabel(unit)

        # Draw extra's (from fewsunblobbed)
        title = None
        y_min, y_max = None, None

        is_empty = True
        for identifier in identifiers:
            location_id = identifier['identifier']
            logger.debug(
                "Find location id {0} in locations".format(location_id))
            location_name = [
                location.description() for location in locations
                if location.identifier == location_id][0]
            logger.debug(
                "Voor timeseries, datasource is {0}".format(self.datasource))

            timeseries = self.datasource.timeseries(
                location_id, start_date, end_date)

            if timeseries is not None:
                is_empty = False
                # Plot data if available.
                dates = timeseries.dates()
                values = timeseries.values()
                if values:
                    graph.axes.plot(
                        dates, values,
                        lw=1,
                        color=line_styles[str(identifier)]['color'],
                        label=location_name)

            if (self.datasource.has_percentiles() and
                hasattr(graph, 'add_percentiles')):
                percentiles = self.datasource.percentiles(
                    location_id, start_date, end_date)
                graph.add_percentiles(location_name, percentiles, (0.4, 0.2))

            # Apply custom layout parameters.
            if 'layout' in identifier:
                layout = identifier['layout']
                if "y_label" in layout:
                    graph.axes.set_ylabel(layout['y_label'])
                if "x_label" in layout:
                    graph.set_xlabel(layout['x_label'])
                apply_lines(identifier, values, location_name)

        if is_empty and raise_404_if_empty:
            raise Http404

        # Originally legend was only turned on if layout.get('legend')
        # was true. However, as far as I can see there is no way for
        # that to become set anymore. Since a legend should always be
        # drawn, we simply put the following:
        graph.legend()

        # If there is data, don't draw a frame around the legend
        if graph.axes.legend_ is not None:
            graph.axes.legend_.draw_frame(False)
        else:
            # TODO: If there isn't, draw a message. Give a hint that
            # using another time period might help.
            pass

        # Extra layout parameters. From lizard-fewsunblobbed.
        y_min_manual = y_min is not None
        y_max_manual = y_max is not None
        if y_min is None:
            y_min, _ = graph.axes.get_ylim()
        if y_max is None:
            _, y_max = graph.axes.get_ylim()

        if title:
            graph.suptitle(title)

        graph.set_ylim(y_min, y_max, y_min_manual, y_max_manual)

        # Copied from lizard-fewsunblobbed.
        if "horizontal_lines" in layout_extra:
            for horizontal_line in layout_extra['horizontal_lines']:
                graph.axes.axhline(
                    horizontal_line['value'],
                    ls=horizontal_line['style']['linestyle'],
                    color=horizontal_line['style']['color'],
                    lw=horizontal_line['style']['linewidth'],
                    label=horizontal_line['name'])

        graph.add_today()
        return graph.render()
