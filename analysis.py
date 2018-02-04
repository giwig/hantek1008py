#!/usr/bin/env python3

import argparse
from typing import List, Dict
import numpy
from utils.common import parse_csv_lines, open_csv_file
import utils.electro
import math
import re
from collections import namedtuple
from utils.csvwriter import CsvWriter
import sys

assert sys.version_info >= (3, 6)


def analyse_channel_window(channel_values: List[float], input_sampling_rate: float):
    length = len(channel_values)
    fourier = numpy.fft.rfft(channel_values * numpy.blackman(length))
    # convert complex -> real
    fourier_amplitude = numpy.absolute(fourier)
    fourier_phase = numpy.angle(fourier, deg=True)  # range: [-180,180]
    fourier_frequency = numpy.fft.rfftfreq(n=length, d=1.0 / input_sampling_rate)
    fourier_frequency_step_width = fourier_frequency[1]

    # get the highest value (+ index of that)
    max_index, max_value = max(enumerate(fourier_amplitude), key=lambda v: v[1])

    # norm_fac = 1.0 / max_value  # max in result will be 1.0
    norm_fac = 2.0 / (length/2)  # see "y-Axis: The Amplitude of the FFT Signal" in http://www.cbcity.de/die-fft-mit-python-einfach-erklaert
    # calculate the frequency that each value in the fourier array belongs to,
    # and than builds pairs of frequency and intensity)
    fft_amplitude_points = list(zip(fourier_frequency, fourier_amplitude * norm_fac))
    # does the same as the code above, but is 5x slower
    # fft_points = [(x / (2 * len(fourier)) * self.__input_sampling_rate, y * norm_fac)
    #               for x, y in enumerate(fourier)]

    # fft_points[0] is y offset (DC value)

    fft_phase_points = list(zip(fourier_frequency, 0.5 + fourier_phase / 360))

    auto = numpy.correlate(channel_values, channel_values, mode="full")
    # fft_autocorrelation_points = list(zip(fourier_frequency, (auto / max(auto))[round(len(auto) / 2):]))

    # print_column('fft freq steps', f'{fourier_frequency_step_width:.9f} Hz')

    # amplitude_trashold = 0.1 * max_value
    # crucial_sins = [(freq, amp, phase) for freq, amp, phase
    #                 in zip(fourier_frequency, fourier_amplitude, fourier_phase)
    #                 if amp >= amplitude_trashold]
    # max_sin = max(crucial_sins, key=lambda v: v[1])
    # print(sep="\n", *(f"{freq:7.3f} Hz, {(max(phase,max_sin[2])-min(phase, max_sin[2])):6.2f}°: {amp:.3f}"
    #                   for freq, amp, phase in crucial_sins))

    main_frequency = fft_amplitude_points[max_index][0]
    main_frequency_phase = fft_phase_points[max_index][0]

    # print_column('main frequency(fft, max)', f'{main_frequency:.4f} Hz + {main_frequency_phase:6.2f}°')
    mf_fft_parabolic = mf_fft_gaussian = None
    if 0 < max_index < len(fft_amplitude_points):
        mf_fft_parabolic = electro.parabolic_interpolation(fourier_amplitude, max_index) * fourier_frequency_step_width
        mf_fft_gaussian = electro.gaussian_interpolation(fourier_amplitude, max_index) * fourier_frequency_step_width

    mf_autocorrelate_parabolic = electro.measure_main_frequency_autocorrelate(channel_values, input_sampling_rate)
    mf_zerocrossing = electro.measure_main_frequency_zero_crossing(channel_values, input_sampling_rate)

    # print_column('min/rms/max', f'{min(channel_values_part):.4f} / '
    #                             f'{electro.rms(channel_values_part):.4f} / '
    #                             f'{max(channel_values_part):.4f}')

    return main_frequency, mf_fft_parabolic, mf_fft_gaussian, mf_autocorrelate_parabolic, mf_zerocrossing


def analyse_pair_window(voltage_channel_values: List[float], ampere_channel_values: List[float],
                        input_sampling_rate: float,
                        voltage_scale_factor: float,
                        voltage_to_ampere_factor: float):
    voltage_channel_values_part = [v * voltage_scale_factor for v in voltage_channel_values]
    ampere_channel_values_part = [v * voltage_to_ampere_factor for v in ampere_channel_values]

    P, Q, S = electro.calc_power(voltage_channel_values_part, ampere_channel_values_part, input_sampling_rate)
    phase_angle = math.acos(P / S)
    voltage_rms = electro.rms(voltage_channel_values_part)
    ampere_rms = electro.rms(ampere_channel_values_part)
    return P, Q, S, phase_angle, voltage_rms, ampere_rms

    # print_column("power (P/Q/S)", f"{P:.4f} W / {Q:.4f} var / {S:.4f} VA")
    # print_column("\->phase angle φ", f"{phase_angle:.4f}°")
    # print_column("voltage rms", f"{volatge_rms:0.4f} V")
    # print_column("ampere rms", f"{ampere_rms:0.4f} A")


VoltAmpChPair = namedtuple("VoltAmpChPair", ["voltage_ch", "ampere_ch", "name"])


def main():

    def va_pair_type(value: str) -> VoltAmpChPair:
        # vaild str eg 1:4
        match = re.match(r"(\d):(\d):([A-Za-z0-9_]+)", value)
        if not match:
            raise argparse.ArgumentTypeError(f"Invalid syntax. Voltage-Ampere-Pairs have to be in"
                                             f"the format: v:a:NAME eg. 1:4:L1")

        volt_amp_ch_pair = VoltAmpChPair(int(match.group(1)),
                                         int(match.group(2)),
                                         match.group(3))

        def check_channel(channel: int):
            if not 1 <= channel <= 8:
                raise argparse.ArgumentTypeError(f"There is no channel {channel}")

        check_channel(volt_amp_ch_pair.voltage_ch)
        check_channel(volt_amp_ch_pair.ampere_ch)

        if volt_amp_ch_pair.voltage_ch == volt_amp_ch_pair.ampere_ch:
            raise argparse.ArgumentTypeError(f"Voltage ({volt_amp_ch_pair.voltage_ch}) "
                                             f"and ampere ({volt_amp_ch_pair.ampere_ch}) channel must be different ")
        return volt_amp_ch_pair

    def channel_type(value):
        ivalue = int(value)
        if 1 <= ivalue <= 2*8:
            return ivalue
        raise argparse.ArgumentTypeError(f"There is no channel {value}")

    def arg_assert(ok, fail_message):
        if not ok:
            parser.error(fail_message)

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("csvfile",
                        type=str, default=None,
                        help="The data file. Can be '-' to take STDIN as data source.")
    # TODO: at the moment channel means column not the real channel used
    #       if e.g. only channel 7 & 8 were recorded, these wer handelt as channel 1&2 in this software
    parser.add_argument("voltamp_pairs", nargs="+",
                        type=va_pair_type, default=None,
                        help="What channels belong together. Format is: "
                             "{volt_channel}:{ampere_channel}:{name} e.g. '1:2:L1'")
    parser.add_argument("-w", "--windowsize", dest="window_size",
                        type=int,  default=2048,
                        help="The size of the window used to analyse the data. One value of "
                             "each type (e.g. frequency, rms voltage) is computed per window.")
    parser.add_argument("-s", "--stepsize", dest="step_size",
                        type=int, default=1024,
                        help="The window is shifted about this amount after each computation round.")
    parser.add_argument("--voltagescale", dest="voltage_scale_factor",
                        type=float, default=200,
                        help="Voltage values are scale with this factor before any analysis happens.")
    parser.add_argument("--v2afactor", dest="voltage_to_ampere_factor",
                        type=float, default=2.857,
                        help="Ampere values are scale with this factor before any analysis happens.")
    # parser.add_argument("-s", "--channels", metavar="channel", nargs="+",
    #                     type=channel_type, default=None,
    #                     help="Select channels that are of interest")

    args = parser.parse_args()

    csv_writer = CsvWriter(sys.stdout, delimiter=',')

    source_csv_file = sys.stdin if args.csvfile == "-" else open_csv_file(args.csvfile)

    # read header (all comment lines before the data)
    header = []
    channel_names_line = ""
    header_data_file_format = "auto"  # new versions of csvexport.py produces CSV files
                                      # that start with '# HEADER and the before the actual data come
                                      # a line with '# DATA' comes
    if header_data_file_format == "auto":
        if args.csvfile == "-":
            header_data_file_format = True
        else:
            header_data_file_format = False

    while True:
        line = source_csv_file.readline()

        if header_data_file_format:
            if line == "# DATA\n":
                break
            if not line.startswith("#"):
                continue
        else:
            if not line.startswith("#"):  # first data line is ignored
                break
        if line.startswith("# ch"):
            channel_names_line = line
        header.append(line)

    device_sampling_rate, measured_sampling_rate, _, per_channel_data = parse_csv_lines(header)

    sampling_rate = ["unknown", *device_sampling_rate, *measured_sampling_rate][-1]
    channel_count = len(channel_names_line.split(","))
    args.voltamp_pairs = [VoltAmpChPair(vap.voltage_ch-1, vap.ampere_ch-1, vap.name) for vap in args.voltamp_pairs]

    # if args.channels is None:
    #     args.channels = [*range(0, channel_count-1)]
    # else:
    #     args.channels = [x - 1 for x in args.channels]

    for vap in args.voltamp_pairs:
        arg_assert(vap.voltage_ch < channel_count, f"Selected voltage channel {vap.voltage_ch+1} does not exist.");
        arg_assert(vap.ampere_ch < channel_count, f"Selected ampere channel {vap.ampere_ch+1} does not exist.");

    csv_writer.write_comment(f"source                : {args.csvfile}")
    csv_writer.write_comment(f"device_sampling_rate  : {device_sampling_rate}")
    csv_writer.write_comment(f"measured_sampling_rate: {measured_sampling_rate}")
    csv_writer.write_comment(f"|->sampling_rate      : {sampling_rate} Hz")
    csv_writer.write_comment(f"channel count         : {channel_count}")
    csv_writer.write_comment(f"voltage ampere pairs  : {', '.join(f'{name}: {v_ch+1} and {a_ch+1}' for v_ch, a_ch, name in args.voltamp_pairs)}")

    values = []
    last_time = None
    # work in Watt*sec
    PQS_work = {pair_name: [0, 0, 0] for _, _, pair_name in args.voltamp_pairs}
    # max_list = [0 for _ in range(0, channel_count)]
    for time, value_row in read_value(source_csv_file):
        # for i in range(0, channel_count):
        #     max_list[i] = max(abs(value_row[i]), max_list[i])
        # continue
        values.append(value_row)

        if len(values) == args.window_size:
            mid_time = time - 0.5 * args.window_size * (1.0/sampling_rate)
            per_channel_data = list(zip(*values))
            print_window_analysis(csv_writer,
                                  mid_time,
                                  0 if last_time is None else mid_time - last_time,
                                  per_channel_data,
                                  args.voltamp_pairs,
                                  PQS_work,
                                  sampling_rate,
                                  args.voltage_scale_factor,
                                  args.voltage_to_ampere_factor)

            del values[0:args.step_size]  # remove unneeded values
            last_time = mid_time
    # print(f"max_l3_i: {max_list} V")
    return


def read_value(csv_file):
    time = None  # the time as unix timestamp (sec since 1970 or so)
    while True:
        line = csv_file.readline()
        if line == "":
            return
        if line.startswith("#"):
            match = common.unix_time_regex.search(line)
            if match:
                time = float(match.group(2))
                # if "UTC" not in line:  # older version of csvexport.py uses local time instead of UTC
                #     time -= 60*60  # older version was only used on on CET so 1h time difference

        elif time is not None:  # ignore values that are before first time comment
            yield time, [float(x) for x in line.split(",")]


def print_window_analysis(csv_writer: CsvWriter,
                          time: float,
                          delta: float,
                          per_channel_data: List[List[float]],
                          voltamp_pairs: VoltAmpChPair,
                          PQS_work: Dict[str, List[float]],
                          input_sampling_rate: float,
                          voltage_scale_factor: float,
                          voltage_to_ampere_factor: float
                          ):
    wattsec_to_wh = 1.0 / (60 * 60)
    time_str = f"{time:.3f}"

    for voltage_ch, ampere_ch, pair_name in voltamp_pairs:
        voltage_data = per_channel_data[voltage_ch]
        ampere_data = per_channel_data[ampere_ch]

        P, Q, S, phase_angle, voltage_rms, ampere_rms = analyse_pair_window(voltage_data,
                                                                            ampere_data,
                                                                            input_sampling_rate,
                                                                            voltage_scale_factor,
                                                                            voltage_to_ampere_factor)
        mf_fft_max, mf_fft_parabolic, mf_fft_gaussian, mf_autocorrelate_parabolic, mf_zerocrossing =\
            analyse_channel_window(voltage_data, input_sampling_rate)

        # work in Watt*sec
        PQS_work[pair_name][0] += P * delta
        PQS_work[pair_name][1] += Q * delta
        PQS_work[pair_name][2] += S * delta

        csv_writer.write_row([time_str, f"{pair_name}_PW", f"{PQS_work[pair_name][0]*wattsec_to_wh:.6f}", "Wh"])
        csv_writer.write_row([time_str, f"{pair_name}_QW", f"{PQS_work[pair_name][1]*wattsec_to_wh:.6f}", "Wh"])
        csv_writer.write_row([time_str, f"{pair_name}_SW", f"{PQS_work[pair_name][2]*wattsec_to_wh:.6f}", "Wh"])

        csv_writer.write_row([time_str, f"{pair_name}_P", f"{P:.3f}", "W"])  # Wirkleistung
        csv_writer.write_row([time_str, f"{pair_name}_Q", f"{Q:.3f}", "W"])
        csv_writer.write_row([time_str, f"{pair_name}_S", f"{S:.3f}", "W"])
        csv_writer.write_row([time_str, f"{pair_name}_φ", f"{phase_angle:.3f}", "°"])
        # _U was _V in an older version
        csv_writer.write_row([time_str, f"{pair_name}_U", f"{voltage_rms:.3f}", "V"])
        # _I was _A in an older version
        csv_writer.write_row([time_str, f"{pair_name}_I", f"{ampere_rms:.3f}", "A"])

        csv_writer.write_row([time_str, f"{pair_name}_F_MAX", f"{mf_fft_max:.6f}", "Hz"])
        csv_writer.write_row([time_str, f"{pair_name}_F_PAR", f"{mf_fft_parabolic if mf_fft_parabolic is not  None else -1:.6f}", "Hz"])
        csv_writer.write_row([time_str, f"{pair_name}_F_GAU", f"{mf_fft_gaussian if mf_fft_gaussian is not None else -1:.6f}", "Hz"])
        csv_writer.write_row([time_str, f"{pair_name}_F_AUT", f"{mf_autocorrelate_parabolic:.6f}", "Hz"])
        csv_writer.write_row([time_str, f"{pair_name}_F_ZC", f"{mf_zerocrossing:.6f}", "Hz"])
    # write sum over all voltamp pairs
    csv_writer.write_row([time_str, f"Li_PW", f"{sum(list(zip(*PQS_work.values()))[0]) * wattsec_to_wh:.6f}", "Wh"])
    csv_writer.write_row([time_str, f"Li_QW", f"{sum(list(zip(*PQS_work.values()))[1]) * wattsec_to_wh:.6f}", "Wh"])
    csv_writer.write_row([time_str, f"Li_SW", f"{sum(list(zip(*PQS_work.values()))[2]) * wattsec_to_wh:.6f}", "Wh"])


if __name__ == '__main__':
    main()
