import sys
import json
import csv
import os
import queue
import datetime
import serial
import traceback
from PyQt5.QtCore import QRunnable, pyqtSlot, pyqtSignal, QObject, QThreadPool
from pglive.sources.data_connector import DataConnector
from pglive.sources.live_axis_range import LiveAxisRange
from pglive.sources.live_plot import LiveLinePlot, LiveScatterPlot
from pglive.sources.live_axis import LiveAxis
from pglive.kwargs import LeadingLine, Crosshair
from pglive.sources.live_plot_widget import LivePlotWidget
from pyqtgraph import *
import pyqtgraph as pg
import pandas as pd
import serial.tools.list_ports as port_list
from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QTableWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidgetItem,
    QApplication,
    QPushButton,
    QTabWidget,
    QFileDialog,
    QLineEdit,
    QInputDialog,
    QComboBox,
)

# Globals
_continue = False


# Start of threading classes
class WorkerSignals(QObject):
    """
    Defines the signals available from a running worker thread.

    Supported signals are:

    finished
        No data

    error
        tuple (exctype, value, traceback.format_exc() )

    result
        object data returned from processing, anything

    progress
        int indicating % progress

    """

    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)
    progress = pyqtSignal(object, object)


class Worker(QRunnable):
    """
    Worker thread

    Inherits from QRunnable to handler worker thread setup, signals and wrap-up.

    :param callback: The function callback to run on this worker thread. Supplied args and
                     kwargs will be passed through to the runner.
    :type callback: function
    :param args: Arguments to pass to the callback function
    :param kwargs: Keywords to pass to the callback function

    """

    def __init__(self, fn, *args, **kwargs):
        super(Worker, self).__init__()

        # Store constructor arguments (re-used for processing)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

        # Add the callback to our kwargs
        self.kwargs["progress_callback"] = self.signals.progress

    @pyqtSlot()
    def run(self):
        """
        Initialise the runner function with passed args, kwargs.
        """

        # Retrieve args/kwargs here; and fire processing using them
        try:
            result = self.fn(*self.args, **self.kwargs)
        except:
            traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.signals.error.emit((exctype, value, traceback.format_exc()))
        else:
            self.signals.result.emit(result)  # Return the result of the processing
        finally:
            self.signals.finished.emit()  # Done


# End of threading classes


def update_date_time():
    seconds = 0
    minutes = 0
    hours = 0
    days = 0
    while True:
        seconds += 1
        if seconds == 60:
            seconds = 0
            minutes += 1
        if minutes == 60:
            minutes = 0
            hours += 1
        if hours == 24:
            hours = 0
            days += 1
        yield days, hours, minutes, seconds


def gather_data(ser_port, table, dc, progress_callback):
    global _continue
    while _continue:
        if ser_port.inWaiting() > 0:
            try:
                line_data = ser_port.readline()
                data_dict = json.loads(line_data)  # try removing decode here!
                print(data_dict)
            except Exception as ex:
                print(f"exception gathering data: {ex}")
                data_dict = None
            try:
                if data_dict is None:
                    # something bad happened
                    raise Exception("you don goofed")

                # print("sending data to call back")
                progress_callback.emit(table, data_dict)
                dc.cb_append_data_point(data_dict['pos_cnt'])

            except Exception as ex:
                print(ex)


class GetConfig(QWidget):
    def __init__(self):
        super().__init__()
        self.config_sent = 0
        self.controller_amount = 0
        self.ser_port_dict = {}
        self.ports = []
        self.layout = QVBoxLayout()
        self.ghr = QLineEdit()
        self.tpi = QLineEdit()
        self.resolution = QLineEdit()
        self.quadrature = QLineEdit()
        self.cycles = QLineEdit()
        self.duty_cycle = QLineEdit()
        self.pico = QComboBox()
        self.stage = QComboBox()
        self.send = QPushButton("Send config")
        self.send.clicked.connect(self.send_data)
        self.send_cycle = QPushButton("Send Cycles")
        self.send_cycle.clicked.connect(self.send_cycles)
        self.config_dict = {
            "ghr": None,
            "tpi": None,
            "resolution": None,
            "quadrature": None,
        }

        self.cycle_dict = {
            "num_of_cycles": None,
            "duty_cycle": None,
        }

        self.stage.addItem("A")
        self.stage.addItem("B")

        self.ghr.setPlaceholderText("Enter ghr here")
        self.tpi.setPlaceholderText("Enter tpi here")
        self.resolution.setPlaceholderText("Enter resolution here")
        self.quadrature.setPlaceholderText("Enter quadrature here")
        self.cycles.setPlaceholderText("Enter number of cycles here")
        self.duty_cycle.setPlaceholderText("Enter the duty cycle % here")

        self.usr_config_inputs = [self.ghr, self.tpi, self.resolution, self.quadrature]
        self.usr_cycle_inputs = [self.cycles, self.duty_cycle]

        self.setLayout(self.layout)
        self.layout.addWidget(self.ghr)
        self.layout.addWidget(self.tpi)
        self.layout.addWidget(self.resolution)
        self.layout.addWidget(self.quadrature)
        self.layout.addWidget(self.cycles)
        self.layout.addWidget(self.duty_cycle)
        self.layout.addWidget(self.pico)
        self.layout.addWidget(self.stage)
        self.layout.addWidget(self.send)
        self.layout.addWidget(self.send_cycle)

    def show(self):
        super().show()
        self.controller_amount, ok = QInputDialog.getInt(
            self, "Controller Count", "How many controllers are you seting up?"
        )

    def update_ports(self, ports):
        self.ports = ports
        for port in self.ports:
            # add the pico port name to a combo box to allow the user to pick the pico they are talking with
            self.pico.addItem(port.name)
            # create a dictionary with the port name as the key to easliy grab the serial object
            self.ser_port_dict.update({port.name: port})

    def send_data(self):
        # update the dictionary with user input values
        for key, value in zip(self.config_dict, self.usr_config_inputs):
            self.config_dict.update({key: value.text()})
        # update the id for the stage
        self.config_dict.update({"id": self.stage.currentText()})
        # create a json object with the dict values
        data = json.dumps(self.config_dict)
        # get the serial port for the pico
        port = self.ser_port_dict.get(self.pico.currentText())
        # add the new line to the message
        data += "\n"
        # send the message
        port.write(data.encode())
        # see what was just sent
        print(data)
        self.config_sent += 1

    def send_cycles(self):
        # this is for the cycle count and duty cycle
        for key, value in zip(self.cycle_dict, self.usr_cycle_inputs):
            if key != 'duty_cycle':
                self.cycle_dict.update({key: value.text()})
            else:
                value = int(value.text())
                value /= 100
                value *= 65536
                print(int(value))
                self.cycle_dict.update({key: str(int(value))})
        data = json.dumps(self.cycle_dict)
        data += "\n"
        port = self.ser_port_dict.get(self.pico.currentText())
        port.write(data.encode())
        if (self.config_sent/2) == self.controller_amount:
            self.config_sent = 0
            self.close()


def update_table(table, data_dict):
    try:
        # Get the index of the last row in the table
        row = table.rowCount()
        # insert a row below the current last row in the table
        table.insertRow(row)
        # get the current date time
        time = datetime.datetime.now()
        # format the date time to hwo we want it
        time = time.strftime("%d-%H:%M:%S")
        # update the table with the values gotten from the controller
        table.setItem(row, 0, QTableWidgetItem(str(time)))
        table.setItem(row, 1, QTableWidgetItem(str(data_dict["cycle count A"])))
        table.setItem(row, 2, QTableWidgetItem(str(data_dict["cycle count B"])))
        table.setItem(row, 3, QTableWidgetItem(str(data_dict["RPM A"])))
        table.setItem(row, 4, QTableWidgetItem(str(data_dict["RPM B"])))
        # scroll to the bottom of the table
        table.scrollToBottom()
    except:
        print(data_dict)


class TableWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ports = []
        self.tables = []
        self.data_collectors = []
        self.queue = queue.Queue()
        self.threadpool = QThreadPool()
        self.setWindowTitle("Data Table")
        self.resize(670, 200)
        self.get_config = GetConfig()

        main = QWidget()
        self.setCentralWidget(main)

        # Class Setup
        self.table_view = QTabWidget()

        layout = QHBoxLayout()
        second = QVBoxLayout()
        main.setLayout(layout)
        layout.addWidget(self.table_view)
        layout.addLayout(second)

        select_picos = QPushButton("Select Picos")
        select_picos.clicked.connect(self.select_picos)
        self.start = QPushButton("Start")
        self.start.clicked.connect(self.start_prog)
        analysis = QPushButton("Analysis")
        analysis.clicked.connect(self.analysis_data)
        self.pause = QPushButton("pause")
        self.pause.clicked.connect(self.pause_prog)
        self.stop = QPushButton("Stop")
        self.stop.clicked.connect(self.stop_prog)
        file_maker = QPushButton("Record data")
        file_maker.clicked.connect(self.record)
        self.clear = QPushButton("Clear tables")
        self.clear.clicked.connect(self.clear_tables)
        self.config = QPushButton("Set test config")
        self.config.clicked.connect(self.get_config_data)

        second.addWidget(select_picos)
        second.addWidget(self.config)
        second.addWidget(self.start)
        second.addWidget(analysis)
        second.addWidget(file_maker)
        second.addWidget(self.clear)
        second.addWidget(self.stop)

        self.start.setEnabled(True)
        self.pause.setEnabled(False)
        self.stop.setEnabled(False)

    def closeEvent(self, a0: QtGui.QCloseEvent):
        super().close()
        global _continue
        _continue = False
        print("Program closing...")

    def start_prog(self):
        global _continue
        _continue = True
        self.start.setEnabled(False)
        self.pause.setEnabled(True)
        self.stop.setEnabled(True)
        print("Starting...")
        start = "start\n"
        for ser in self.ports:
            print("starting stage(s) on port", ser.name)
            ser.write(start.encode("utf-8"))
        self.start_thread_pool()

    def pause_prog(self):
        global _continue
        _continue = False
        self.start.setEnabled(True)
        self.pause.setEnabled(False)
        print("Pausing....")
        for ser in self.ports:
            ser.write("pause\n".encode())

    def stop_prog(self):
        global _continue
        _continue = False
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        print("Stopping...")
        for ser in self.ports:
            ser.write("stop\n".encode())

    def create_tables(self):
        for p in self.ports:
            table = QTableWidget()
            header_labels = [
                "timestamp",
                "Cycle Count A",
                "Cycle Count B",
                "RPM A",
                "RPM B",
            ]

            # Initialize Table
            table.setColumnCount(len(header_labels))
            table.setHorizontalHeaderLabels(header_labels)
            for i in range(table.columnCount()):
                table.setColumnWidth(i, 150)

            self.table_view.addTab(table, f"{p.name} Table")
            self.tables.append(table)

    def create_graphs(self):
        for p in self.ports:
            left_axis = LiveAxis("left", axisPen="red", textPen="red")
            bottom_axis = LiveAxis(
                "bottom",
                axisPen="red",
                textPen="red",
            )

            kwargs = {
                Crosshair.ENABLED: True,
                Crosshair.LINE_PEN: pg.mkPen(color="red", width=1),
                Crosshair.TEXT_KWARGS: {"color": "green"},
            }

            live_plot_widget = LivePlotWidget(
                title="Stage positional data @ 4Hz",
                axisItems={"bottom": bottom_axis, "left": left_axis},
                x_range_controller=LiveAxisRange(roll_on_tick=150, offset_left=1.5),
                **kwargs,
            )

            live_plot_widget.x_range_controller.crop_left_offset_to_data = True
            plot = LiveLinePlot(pen="purple")
            plot.set_leading_line(
                LeadingLine.VERTICAL, pen=pg.mkPen("green"), text_axis=LeadingLine.AXIS_Y
            )

            live_plot_widget.addItem(plot)
            data_connector = DataConnector(plot, max_points=15000, update_rate=1000)
            self.data_collectors.append(data_connector)
            self.table_view.addTab(live_plot_widget, f"{p.name} Graph")

    def select_picos(self):
        ports = list(port_list.comports())
        for port in ports:
            print(port.name)
            # For linux
            if "ACM" in port.name:
                ser_port = serial.Serial(
                    f"/dev/{port.name}",
                    baudrate=115200,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    bytesize=serial.EIGHTBITS,
                    timeout=1,
                )
                self.ports.append(ser_port)
            # For windows
            if "COM" in port.name:
                ser_port = serial.Serial(
                    f"{port.name}",
                    baudrate=115200,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    bytesize=serial.EIGHTBITS,
                    timeout=1,
                )
                self.ports.append(ser_port)

        self.create_tables()
        self.create_graphs()

    def get_config_data(self):
        self.get_config.update_ports(self.ports)
        self.get_config.show()

    def start_thread_pool(self):
        for ser_port, table, dc in zip(self.ports, self.tables, self.data_collectors):
            worker = Worker(gather_data, ser_port, table, dc)
            worker.signals.progress.connect(update_table)
            self.threadpool.start(worker)

    def record(self):
        for table in self.tables:
            filename, ok = QFileDialog.getSaveFileName(self, "Save File", "", "*.csv")
            if filename:
                # check if file is open
                if os.path.exists(filename):
                    os.remove(filename)

                # write the data
                try:
                    for row in range(table.rowCount()):
                        _list = []
                        for col in range(table.columnCount()):
                            item = table.item(row, col).text()
                            _list.append(item)

                        with open(filename, "a") as csvfile:
                            csvwriter = csv.writer(csvfile)
                            csvwriter.writerow(_list)
                    print("finished creating file...")

                except Exception as ex:
                    print(ex)
                    print("error creating file")
            else:
                print("no file created")

    def clear_tables(self):
        for table in self.tables:
            while table.rowCount() > 0:
                table.removeRow(0)

    def analysis_data(self):
        filename, ok = QFileDialog.getOpenFileName(
            self, "Select a File", "D:\\icons\\avatar\\", "Images (*.png *.jpg)"
        )

        csv = filename
        rpms = pd.read_csv(csv, low_memory=False)
        rpms.drop("Time", axis=1)

        stats_for_a = rpms[rpms["A"] > "2500"]  # filters results
        stats_for_b = rpms[rpms["B"] > "2500"]
        print(stats_for_a.describe())
        print(stats_for_b.describe())


app = QApplication(sys.argv)
window = TableWindow()
window.show()
sys.exit(app.exec())
