from __future__ import division, absolute_import

import math
import time
import types
from collections import deque
from itertools import groupby

import six
import numpy as np
import matplotlib.pyplot as plt

from neupy.utils import preformat_value, AttributeKeyDict
from neupy.helpers import table
from neupy.core.base import BaseSkeleton
from neupy.core.properties import (BoundedProperty, NumberProperty,
                                   Property)
from neupy.layers.connections import LayerConnection
from .utils import (iter_until_converge, shuffle, normalize_error,
                    StopNetworkTraining)


__all__ = ('BaseNetwork',)


def show_epoch_summary(network):
    delay_limit = 1.  # delay time in seconds
    prev_summary_time = None
    delay_history_length = 10
    terminal_output_delays = deque(maxlen=delay_history_length)

    table_drawer = table.TableDrawer(
        table.Column(name="Epoch #"),
        table.NumberColumn(name="Train err"),
        table.NumberColumn(name="Valid err"),
        table.TimeColumn(name="Time", width=10),
        stdout=network.logs.write
    )
    table_drawer.start()

    try:
        yield

        while True:
            now = time.time()

            if prev_summary_time is not None:
                time_delta = now - prev_summary_time
                terminal_output_delays.append(time_delta)

            table_drawer.row([
                network.last_epoch,
                network.errors.last() or '-',
                network.validation_errors.last() or '-',
                network.training.epoch_time,
            ])
            prev_summary_time = now

            if len(terminal_output_delays) == delay_history_length:
                prev_summary_time = None
                average_delay = np.mean(terminal_output_delays)

                if average_delay < delay_limit:
                    show_epoch = int(
                        network.training.show_epoch *
                        math.ceil(delay_limit / average_delay)
                    )
                    table_drawer.line()
                    table_drawer.message("Too many outputs in a terminal.")
                    table_drawer.message("Set up logging after each {} epochs"
                                         "".format(show_epoch))
                    table_drawer.line()
                    terminal_output_delays.clear()

                    network.training.show_epoch = show_epoch
            yield

    finally:
        table_drawer.finish()
        network.logs.newline()


def show_network_options(network, highlight_options=None):
    """ Display all available parameters options for Neural Network.

    Parameters
    ----------
    network : object
        Neural network instance.
    highlight_options : list
        List of enabled options. In that case all options from that
        list would be marked with a green color.
    """
    available_classes = [cls.__name__ for cls in network.__class__.__mro__]
    logs = network.logs

    if highlight_options is None:
        highlight_options = {}

    def group_by_class_name(value):
        _, option = value
        option_priority = -available_classes.index(option.class_name)
        return option_priority, option.class_name

    grouped_options = groupby(
        sorted(network.options.items(), key=group_by_class_name),
        group_by_class_name,
    )

    has_layer_structure = (
        hasattr(network, 'connection') and
        isinstance(network.connection, LayerConnection)
    )
    logs.title("Main information")
    logs.message("ALGORITHM", network.class_name())
    if has_layer_structure:
        logs.message("ARCHITECTURE", network.connection)

    logs.title("Network options")

    for (_, class_name), options in grouped_options:
        if not options:
            continue

        logs.write("{}:".format(class_name))
        for key, data in sorted(options):
            if key in highlight_options:
                msg_color = 'green'
                value = highlight_options[key]
            else:
                msg_color = 'gray'
                value = data.value

            formated_value = preformat_value(value)
            msg_text = "{} = {}".format(key, formated_value)
            logs.message("OPTION", msg_text, color=msg_color)

        logs.write("")


def logging_info_about_the_data(network, input_train, input_test):
    logs = network.logs
    n_train_samples = input_train.shape[0]
    train_feature_shape = input_train.shape[1:]

    logs.title("Start train")
    logs.message("TRAIN DATA",
                 "{} samples, feature shape: {}"
                 "".format(n_train_samples, train_feature_shape))

    if input_test is not None:
        n_test_samples = input_test.shape[0]
        test_feature_shape = input_test.shape[1:]
        logs.message("TEST DATA",
                     "{} samples, feature shape: {}"
                     "".format(n_test_samples, test_feature_shape))


def logging_info_about_training(network, epochs, epsilon):
    logs = network.logs
    if epsilon is None:
        logs.message("TRAINING", "Total epochs: {}".format(epochs))
    else:
        logs.message("TRAINING", "Epsilon: {}, Max epochs: {}"
                                 "".format(epsilon, epochs))


def parse_show_epoch_property(network, n_epochs, epsilon=None):
    show_epoch = network.show_epoch

    if isinstance(show_epoch, int):
        return show_epoch

    if epsilon is not None and isinstance(show_epoch, six.string_types):
        network.logs.warning("Can't use `show_epoch` value in converging "
                             "mode. Set up `show_epoch` property equal to 1")
        return 1

    number_end_position = show_epoch.index('time')
    # Ignore grammar mistakes like `2 time`, this error could be
    # really annoying
    n_epochs_to_check = int(show_epoch[:number_end_position].strip())

    if n_epochs <= n_epochs_to_check:
        return 1

    return int(round(n_epochs / n_epochs_to_check))


def create_training_epochs_iterator(network, epochs, epsilon=None):
    if epsilon is not None:
        return iter_until_converge(network, epsilon, max_epochs=epochs)

    next_epoch = network.last_epoch + 1
    return range(next_epoch, next_epoch + epochs)


class ShowEpochProperty(BoundedProperty):
    """ Class helps validate specific syntax for `show_epoch`
    property from ``BaseNetwork`` class.

    Parameters
    ----------
    {BoundedProperty.minval}
    {BoundedProperty.maxval}
    {BaseProperty.default}
    {BaseProperty.required}
    """
    expected_type = tuple([int] + [six.string_types])

    def validate(self, value):
        if not isinstance(value, six.string_types):
            if value < 1:
                raise ValueError("Property `{}` value should be integer "
                                 "greater than zero or string. See the "
                                 "documentation for more information."
                                 "".format(self.name))
            return

        if 'time' not in value:
            raise ValueError("`{}` value has invalid string format."
                             "".format(self.name))

        valid_endings = ('times', 'time')
        number_end_position = value.index('time')
        number_part = value[:number_end_position].strip()

        if not value.endswith(valid_endings) or not number_part.isdigit():
            valid_endings_formated = ', '.join(valid_endings)
            raise ValueError(
                "Property `{}` in string format should be a positive number "
                "with one of those endings: {}. For example: `10 times`."
                "".format(self.name, valid_endings_formated)
            )

        if int(number_part) < 1:
            raise ValueError("Part that related to the number in `{}` "
                             "property should be an integer greater or "
                             "equal to one.".format(self.name))


class ErrorHistoryList(list):
    """ Wrapper around the built-in list class that adds additional
    methods.
    """
    def last(self):
        """ Returns last element if list is not empty,
        ``None`` otherwise.
        """
        if self and self[-1] is not None:
            return normalize_error(self[-1])

    def previous(self):
        """ Returns last element if list is not empty,
        ``None`` otherwise.
        """
        if len(self) > 2 and self[-2] is not None:
            return normalize_error(self[-2])

    def normalized(self):
        """ Normalize list that contains error outputs.

        Returns
        -------
        list
            Return the same list with normalized values if there
            where some problems.
        """
        if not self or isinstance(self[0], float):
            return self

        normalized_errors = map(normalize_error, self)
        return list(normalized_errors)


class BaseNetwork(BaseSkeleton):
    """ Base class for Neural Network algorithms.

    Parameters
    ----------
    step : float
        Learning rate, defaults to ``0.1``.
    show_epoch : int or str
        This property controls how often the network will display information
        about training. There are two main syntaxes for this property.
        You can describe it as positive integer number and it
        will describe how offen would you like to see summary output in
        terminal. For instance, number `100` mean that network will show you
        summary in 100, 200, 300 ... epochs. String value should be in a
        specific format. It should contain the number of times that the output
        will be displayed in the terminal. The second part is just
        a syntax word ``time`` or ``times`` just to make text readable.
        For instance, value ``'2 times'`` mean that the network will show
        output twice with approximately equal period of epochs and one
        additional output would be after the finall epoch.
        Defaults to ``1``.
    shuffle_data : bool
        If it's ``True`` class shuffles all your training data before
        training your network, defaults to ``True``.
    epoch_end_signal : function
        Calls this function when train epoch finishes.
    train_end_signal : function
        Calls this function when train process finishes.
    {Verbose.verbose}

    Attributes
    ----------
    errors : ErrorHistoryList
        Contains list of training errors. This object has the same
        properties as list and in addition there are three additional
        useful methods: `last`, `previous` and `normalized`.
    train_errors : ErrorHistoryList
        Alias to `errors` attribute.
    validation_errors : ErrorHistoryList
        The same as `errors` attribute, but it contains only validation
        errors.
    last_epoch : int
        Value equals to the last trained epoch. After initialization
        it is equal to ``0``.

    Methods
    -------
    plot_errors(logx=False)
        Draws the error rate update plot. It always shows network
        learning progress. When you add cross validation data set
        into training function it displays validation data set error as
        separated curve. If parameter ``logx`` is equal to the
        ``True`` value it displays x-axis in logarithmic scale.
    """
    step = NumberProperty(default=0.1, minval=0)

    show_epoch = ShowEpochProperty(minval=1, default=1)
    shuffle_data = Property(default=False, expected_type=bool)

    epoch_end_signal = Property(expected_type=types.FunctionType)
    train_end_signal = Property(expected_type=types.FunctionType)

    def __init__(self, *args, **options):
        self.errors = self.train_errors = ErrorHistoryList()
        self.validation_errors = ErrorHistoryList()
        self.training = AttributeKeyDict()
        self.last_epoch = 0

        super(BaseNetwork, self).__init__(*args, **options)
        self.init_properties()

        if self.verbose:
            show_network_options(self, highlight_options=options)

    def init_properties(self):
        """ Setup default values before populate the options.
        """

    def predict(self, input_data):
        """ Return prediction results for the input data. Output result
        includes post-processing step related to the final layer that
        transforms output to convenient format for end-use.

        Parameters
        ----------
        input_data : array-like

        Returns
        -------
        array-like
        """

    def on_epoch_start_update(self, epoch):
        """ Function would be trigger before run all training procedure
        related to the current epoch.

        Parameters
        ----------
        epoch : int
            Current epoch number.
        """
        self.last_epoch = epoch

    def train_epoch(self, input_train, target_train=None):
        raise NotImplementedError()

    def prediction_error(self, input_test, target_test):
        raise NotImplementedError()

    def train(self, input_train, target_train=None, input_test=None,
              target_test=None, epochs=100, epsilon=None):
        """ Method train neural network.

        Parameters
        ----------
        input_train : array-like
        target_train : array-like or Npne
        input_test : array-like or None
        target_test : array-like or None
        epochs : int
            Defaults to `100`.
        epsilon : float or None
        """

        show_epoch = self.show_epoch
        logs = self.logs
        training = self.training = AttributeKeyDict()

        if epochs <= 0:
            raise ValueError("Number of epochs needs to be greater than 0.")

        if epsilon is not None and epochs <= 2:
            raise ValueError("Network should train at teast 3 epochs before "
                             "check the difference between errors")

        logging_info_about_the_data(self, input_train, input_test)
        logging_info_about_training(self, epochs, epsilon)
        logs.write("")

        iterepochs = create_training_epochs_iterator(self, epochs, epsilon)
        show_epoch = parse_show_epoch_property(self, epochs, epsilon)
        training.show_epoch = show_epoch

        epoch_summary = show_epoch_summary(self)
        next(epoch_summary)

        # Storring attributes and methods in local variables we prevent
        # useless __getattr__ call a lot of times in each loop.
        # This variables speed up loop in case on huge amount of
        # iterations.
        errors = self.errors
        validation_errors = self.validation_errors
        shuffle_data = self.shuffle_data

        train_epoch = self.train_epoch
        epoch_end_signal = self.epoch_end_signal
        train_end_signal = self.train_end_signal
        on_epoch_start_update = self.on_epoch_start_update

        is_first_iteration = True
        can_compute_validation_error = (input_test is not None)
        last_epoch_shown = 0

        with logs.disable_user_input():
            for epoch in iterepochs:
                epoch_start_time = time.time()
                on_epoch_start_update(epoch)

                if shuffle_data:
                    input_train, target_train = shuffle(input_train,
                                                        target_train)
                try:
                    train_error = train_epoch(input_train, target_train)

                    if can_compute_validation_error:
                        validation_error = self.prediction_error(input_test,
                                                                 target_test)
                        validation_errors.append(validation_error)

                    # It's important that we store error result after
                    # we stored validation error.
                    errors.append(train_error)

                    epoch_finish_time = time.time()
                    training.epoch_time = epoch_finish_time - epoch_start_time

                    if epoch % training.show_epoch == 0 or is_first_iteration:
                        next(epoch_summary)
                        last_epoch_shown = epoch

                    if epoch_end_signal is not None:
                        epoch_end_signal(self)

                    is_first_iteration = False

                except StopNetworkTraining as err:
                    # TODO: This notification breaks table view in terminal.
                    # I need to show it in a different way. Maybe I can
                    # send it in generator using ``throw`` method.
                    logs.message("TRAIN", "Epoch #{} stopped. {}"
                                          "".format(epoch, str(err)))

            if epoch != last_epoch_shown:
                next(epoch_summary)

            if train_end_signal is not None:
                train_end_signal(self)

            epoch_summary.close()

        logs.message("TRAIN", "Trainig finished")

    def plot_errors(self, logx=False, ax=None, show=True):
        """ Plot line plot that shows training progress. x-axis
        is an epoch number and y-axis is an error.

        Parameters
        ----------
        logx : bool
            Parameter set up logarithmic scale to x-axis.
            Defaults to ``False``.
        ax : object or None
            Matplotlib axis object. ``None`` values means that axis equal
            to the current one (the same as ``ax = plt.gca()``).
            Defaults to ``None``.
        show : bool
            If parameter is equal to ``True`` plot will instantly shows
            the plot. Defaults to ``True``.

        Returns
        -------
        object
            Matplotlib axis instance.
        """

        if ax is None:
            ax = plt.gca()

        if not self.errors:
            self.logs.warning("There is no data to plot")
            return ax

        train_errors = self.errors.normalized()
        validation_errors = self.validation_errors.normalized()

        if len(train_errors) != len(validation_errors):
            self.logs.warning("Number of train and validation errors are "
                              "not the same. Ignored validation errors.")
            validation_errors = []

        errors_range = np.arange(len(train_errors))
        plot_function = ax.semilogx if logx else ax.plot

        line_error_in, = plot_function(errors_range, train_errors)

        if validation_errors:
            line_error_out, = plot_function(errors_range, validation_errors)
            ax.legend(
                [line_error_in, line_error_out],
                ['Train', 'Validation']
            )

        ax.set_title('Training perfomance')
        ax.set_ylim(bottom=0)

        ax.set_ylabel('Error')
        ax.set_xlabel('Epoch')

        if show:
            plt.show()

        return ax
