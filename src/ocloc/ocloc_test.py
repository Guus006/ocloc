#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCloC - Python Package for OBS Clock Corrections.
Created on Wed Mar 24 15:58:16 2021
modified June 2, 2021
@author: davidnaranjo
"""

from contextlib import contextmanager
import obspy
from obspy.geodetics.base import gps2dist_azimuth
from obspy.signal.cross_correlation import correlate, xcorr_max
import os
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
#from mpl_toolkits.basemap import Basemap
import numpy as np
import pandas as pd
from pathlib import Path
import seaborn as sns
import subprocess
import sys

# Public functions.


def read_xcorrelations(station1_code, station2_code, path2data_dir):
    '''
    Function to load all the available cross-correlation for a given station
    pair. All the correlation files should be in the same directory and should
    have the following structure:
    f= station1_station2_averageDateOfCrosscorrelations_numberOfDatesCorrelated

    Parameters
    ----------
    station1_code : string
        name of the station.
    station2_code : string
        name of the station.
    directory : string
        path to data directory

    Returns
    -------
    corr_st : obspy.Stream()
    corr_dirs : list. directory paths of the files

    '''
    if station1_code == station2_code:
        print('No autocorrelations allowed')
        raise
    files = os.listdir(path2data_dir)

    # Correlation files in a list to return.
    correlation_stream = obspy.Stream()
    correlation_paths = []
    for file in files:
        if station1_code not in file:
            continue
        if station2_code not in file:
            continue
        if '.sac' not in file:
            continue

        correlation_dir = os.path.join(path2data_dir, file)
        correlation_tr = obspy.read(correlation_dir)[0]

        # The file header contains the day in the middle of the correlation
        # averaged over the available days
        average_date = obspy.UTCDateTime(int(file.split('_')[2]))
        number_days = float(file.split('_')[-1].replace('.sac', ''))

        correlation_tr.stats.average_date = average_date
        correlation_tr.stats.number_of_days = number_days
        correlation_tr.stats.station_pair = station1_code + '_' + station2_code
        correlation_stream += correlation_tr
        correlation_paths.append(correlation_dir)
    return correlation_stream, correlation_paths


def read_correlation_file(path2file):
    '''
    Function to load correlation file using the path to the file.

    Parameters
    ----------
    path2file : string
        path to the stream file.

    Returns
    -------
    trace : obspy.Trace()

    '''
    if os.path.isfile(path2file) == False:
        msg = "The file does not exist."
        raise Exception(msg)

    file = os.path.basename(path2file)
    # The file header contains the day in the middle of the correlation
    # averaged over the available days
    splitted_file = file.split("_")
    average_date = obspy.UTCDateTime(int(splitted_file[2]))
    number_days = float(splitted_file[-1].replace('.sac', ''))
    station1_code, station2_code = (str(splitted_file[0]),
                                    str(splitted_file[1]))

    correlation_tr = obspy.read(path2file)[0]
    correlation_tr.stats.average_date = average_date
    correlation_tr.stats.number_of_days = number_days
    correlation_tr.stats.station_pair = station1_code + '_' + station2_code

    return correlation_tr


def trim_correlation_trace(tr, min_t, max_t, freqmin=0.15, freqmax=0.3):
    '''
    Function to trim a cross-correlation trace where the zero is located in
    the middle of the trace. If you want to trim a trace 5 seconds
    before the zero and 10 seconds after the zero then the fucntion should be
    used as: trim_correltation_trace(tr, min_t=-5, max_t=10)

    Parameters
    ----------
    tr : TYPE
        DESCRIPTION.
    min_t : TYPE
        DESCRIPTION.
    max_t : TYPE
        DESCRIPTION.

    Returns
    -------
    times2 : TYPE
        DESCRIPTION.
    data : TYPE
        DESCRIPTION.

    '''
    # limit = (len(tr.data) / 2.) * tr.stats.delta
    # timevec = np.arange(-limit, limit, tr.stats.delta)

    start = -tr.times()[-1] / 2.
    end = tr.times()[-1] / 2.
    times = np.linspace(start, end, tr.stats.npts)

    for i, (amp, t) in enumerate(zip(tr.data, times)):
        if t > min_t:
            low_index = i
            break
    for i, (amp, t) in enumerate(zip(tr.data, times)):
        if t < max_t:
            high_index = i
    tr2 = tr.copy().filter('bandpass', freqmin=freqmin, freqmax=freqmax,
                           corners=4, zerophase=True)

    times2 = times[low_index: high_index]
    data = tr2.data[low_index: high_index]
    return times2, data


def calculate_apriori_dt(cd, correlations, plot=False, **kwargs):
    '''
    Calculates de apriori estimate of given several correlation files of the
    same station pair, given that the correlation was perform in the same order
    for all the files (meaning station 1 is the same and station 2 is the
                       same)

    Parameters
    ----------
    cd : Clock_drift()
        DESCRIPTION.
    correlations : list
      list of Correlations object. You can use the following function
      to retrieve all the correlations for a given station pair:
      correlations = Clock_drift.get_correlations_of_stationpair(station1_code,
                                                            station2_code)
    if plot is set to tru provide a min_t and t_max to trim the correlation
    in the times you want to check

    Returns
    -------
    None.

    '''
    # Check that all correlations have the same processing params.
    freqmins = [c.processing_parameters.freqmin for c in correlations]
    freqmaxs = [c.processing_parameters.freqmax for c in correlations]
    freqmin = list(set(freqmins))
    freqmax = list(set(freqmaxs))
    if len(freqmin) != 1 or len(freqmax) != 1:
        raise Exception('The processing parameters are different for each'
                        + 'correlation')
    freqmin = freqmin[0]
    freqmax = freqmax[0]
    if len(correlations) < 2:
        msg = "There should be at least two correlations to use this method"
        raise Exception(msg)

    sta1 = list(set([correlation.station1_code for correlation in
                     correlations]))
    sta2 = list(set([correlation.station2_code for correlation in
                     correlations]))
    if len(sta1) != 1 or len(sta2) != 1:
        msg = "The first and second station in the correlations are not the "
        msg += "same for all the correlations."
        raise Exception(msg)

    avg_dates = [correlation.average_date for correlation in correlations]

    # Read the correlation of the earliest date
    earliest_date = min(avg_dates)
    earliest_index = avg_dates.index(earliest_date)
    earliest_path2file = correlations[earliest_index].file_path
    earliest_tr = read_correlation_file(path2file=earliest_path2file)
    earliest_tr = earliest_tr.filter('bandpass', freqmin=freqmin,
                                     freqmax=freqmax, corners=4,
                                     zerophase=True)

    # Read the correlation with the latest date.
    latest_date = max(avg_dates)
    latest_index = avg_dates.index(latest_date)
    latest_path2file = correlations[latest_index].file_path
    latest_tr = read_correlation_file(path2file=latest_path2file)
    latest_tr = latest_tr.filter('bandpass', freqmin=freqmin, freqmax=freqmax,
                                 corners=4, zerophase=True)

    cc = correlate(earliest_tr.data, latest_tr.data, 1000)
    shift, value = xcorr_max(cc, abs_max=False)
    time_shift = shift / earliest_tr.stats.sampling_rate

    delta_t = (latest_date - earliest_date)
    shift_rate = time_shift / delta_t

    for correlation in correlations:
        t = correlation.average_date
        dt = (t - earliest_date) * shift_rate
        if cd.get_station(correlation.station1_code).needs_correction:
            correlation.apriori_dt1 = dt
            correlation.apriori_dt2 = 0
        elif cd.get_station(correlation.station2_code).needs_correction:
            correlation.apriori_dt1 = 0
            correlation.apriori_dt2 = dt
        else:
            raise

    if plot:
        min_t = kwargs['min_t']
        max_t = kwargs['max_t']
        t1, data1 = trim_correlation_trace(earliest_tr, min_t, max_t, freqmin,
                                           freqmax)
        t2, data2 = trim_correlation_trace(latest_tr, min_t, max_t, freqmin,
                                           freqmax)
        f, (ax1, ax2) = plt.subplots(2, 1, sharey=True, figsize=(8, 6))
        ax1.set_title('Before correction ' + earliest_tr.stats.station_pair)
        ax2.set_title('After correction ' + earliest_tr.stats.station_pair)
        ax1.plot(t1, data1, label=earliest_tr.stats.average_date)
        ax1.plot(t2, data2, label=latest_tr.stats.average_date)
        ax2.plot(t1, data1, label=earliest_tr.stats.average_date)
        ax2.plot(t2 + time_shift, data2, label=latest_tr.stats.average_date)
        ax2.set_xlabel('Time [s]')
        ax2.set_ylabel('Amplitudes')
        ax1.set_ylabel('Amplitudes')
        plt.tight_layout()
        ax1.legend(loc='best')
        ax2.legend(loc='best')
        plt.show()


def calculate_apriori_dt_corrected(cd, correlations, plot=False, **kwargs):
    '''
    Calculates de apriori estimate of given several correlation files of the
    same station pair, given that the correlation was perform in the same order
    for all the files (meaning station 1 is the same and station 2 is the same)

    Parameters
    ----------
    cd : Clock_drift()
        DESCRIPTION.
    correlations : list
      list of Correlations object. You can use the following function
      to retrieve all the correlations for a given station pair:
      correlations = Clock_drift.get_correlations_of_stationpair(station1_code,
                                                            station2_code)
    if plot is set to tru provide a min_t and t_max to trim the correlation
    in the times you want to check

    Returns
    -------
    None.

    '''
    # Check that all correlations have the same processing params.
    freqmins = [c.processing_parameters.freqmin for c in correlations]
    freqmaxs = [c.processing_parameters.freqmax for c in correlations]
    freqmin = list(set(freqmins))
    freqmax = list(set(freqmaxs))
    if len(freqmin) != 1 or len(freqmax) != 1:
        raise Exception('The processing parameters are different for each'
                        + 'correlation')
    freqmin = freqmin[0]
    freqmax = freqmax[0]
    if len(correlations) < 2:
        msg = "There should be at least two correlations to use this method"
        raise Exception(msg)

    sta1 = list(set([correlation.station1_code for correlation in
                     correlations]))
    sta2 = list(set([correlation.station2_code for correlation in
                     correlations]))
    if len(sta1) != 1 or len(sta2) != 1:
        msg = "The first and second station in the correlations are not the "
        msg += "same for all the correlations."
        raise Exception(msg)

    avg_dates = [correlation.average_date for correlation in correlations]

    # Read the correlation of the earliest date
    earliest_date = min(avg_dates)
    earliest_index = avg_dates.index(earliest_date)
    earliest_path2file = correlations[earliest_index].file_path
    earliest_tr = read_correlation_file(path2file=earliest_path2file)
    earliest_tr = earliest_tr.filter('bandpass', freqmin=freqmin,
                                     freqmax=freqmax, corners=4,
                                     zerophase=True)

    # Read the correlation with the latest date.
    latest_date = max(avg_dates)
    latest_index = avg_dates.index(latest_date)
    latest_path2file = correlations[latest_index].file_path
    latest_tr = read_correlation_file(path2file=latest_path2file)
    latest_tr = latest_tr.filter('bandpass', freqmin=freqmin, freqmax=freqmax,
                                 corners=4, zerophase=True)

    cc = correlate(earliest_tr.data, latest_tr.data, 1000)
    shift, value = xcorr_max(cc, abs_max=False)
    time_shift = shift / earliest_tr.stats.sampling_rate

    delta_t = (latest_date - earliest_date)
    shift_rate = time_shift / delta_t

    for correlation in correlations:
        t = correlation.average_date
        dt = (t - earliest_date) * shift_rate

        sta1 = cd.get_station(correlation.station1_code)
        sta2 = cd.get_station(correlation.station2_code)
        if sta1.needs_correction:
            if sta2.needs_correction:
                correlation.apriori_dt1 = -dt / 2
                correlation.apriori_dt2 = -dt / 2
            else:
                correlation.apriori_dt1 = -dt
                correlation.apriori_dt2 = 0
        elif sta2.needs_correction:
            correlation.apriori_dt1 = 0
            correlation.apriori_dt2 = -dt
        else:
            raise


def plot_stream(st, freqmin=0.15, freqmax=0.3, min_t=-40, max_t=30):
    '''
    Function to generate plot of the cross-correlations before and after
    applying the correction using the apriori estimate function.

    Parameters
    ----------
    station1_code : TYPE
        DESCRIPTION.
    station2_code : TYPE
        DESCRIPTION.

    Returns
    -------
    None.

    '''
    f, ax1 = plt.subplots(1, 1, sharey=True, dpi=300)
    for tr in st:
        t1, data = trim_correlation_trace(tr, min_t, max_t, freqmin, freqmax)
        ax1.plot(t1, data, label=str(tr.stats.average_date)[:10])

    ax1.set_title(tr.stats.station_pair)
    ax1.set_ylabel('Amplitudes')
    ax1.legend(loc=2)
    plt.tight_layout()
    plt.show()


def correlations_of_station_exist(station_code, path2data_dir):
    '''
    Function that returns True if there are correlation files for station.
    Remember that the file must contain the station code in the name.
    '''
    for file in os.listdir(path2data_dir):
        if station_code in file:
            return True
    return False


@contextmanager
def suppress_stdout():
    '''
    Function to hide the ouput in the terminal to make some of the processes
    faster without the need of seing the terminal's output.

    Returns
    -------
    None.

    '''
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout


def check_input_correlation_list(correlation_list):
    '''
    Checks for input of some methods.

    Parameters
    ----------
    correlations_list : TYPE
        DESCRIPTION.

    Returns
    -------
    None.

    '''
    if not isinstance(correlation_list, list):
        err = "correlations_list should be a list of Correlation objects"
        raise Exception(err)
    if len(correlation_list) == 0:
        print("No elements in correlations_list")
    else:
        for c in correlation_list:
            str_type = "<class 'oclock.Correlation'>"
            if str(type(c)) != str_type:
                raise Exception("list doesn't contain Correlation objects")


def correlations_with_parameters(correlation_list, parameters):
    '''
    Method that receives a list of correlations and checks which correlations
    have the processing parameters given.

    Parameters
    ----------
    correlations : TYPE
        DESCRIPTION.
    parameters : TYPE
        DESCRIPTION.

    Returns
    -------
    correlations : TYPE
        DESCRIPTION.

    '''
    check_input_correlation_list(correlation_list)
    correlations = []
    for correlation in correlation_list:
        if correlation.processing_parameters == parameters:
            correlations.append(correlation)
    return correlations


def correlations_of_station(correlation_list, station_code):
    '''
    Parameters
    ----------
    correlations : TYPE
        DESCRIPTION.
    station_code : TYPE
        DESCRIPTION.

    Returns
    -------
    correlations : TYPE
        DESCRIPTION.

    '''
    check_input_correlation_list(correlation_list)
    correlations = []
    for correlation in correlation_list:
        if (correlation.station1_code == station_code or
                correlation.station2_code == station_code):

            correlations.append(correlation)
    return correlations


def correlations_with_average_date(correlation_list, average_date):
    '''
    Parameters
    ----------
    correlations : TYPE
        DESCRIPTION.
    station_code : TYPE
        DESCRIPTION.

    Returns
    -------
    correlations : TYPE
        DESCRIPTION.

    '''
    check_input_correlation_list(correlation_list)
    correlations = []
    for correlation in correlation_list:
        if correlation.average_date == average_date:
            correlations.append(correlation)
    return correlations


def min_number_correlations(stations, correlation_list, min_no_corr):
    '''
    Given a min number of correlations, the function looks for which
    stations have less correlations than the min_no_corr

    Parameters
    ----------
    min_no_corr : int
        DESCRIPTION.

    Returns
    -------
    stations_that_dont_meet_criteria : TYPE
        DESCRIPTION.

    '''
    min_no_corr = int(min_no_corr)
    check_input_correlation_list(correlation_list)
    stations_that_dont_meet_criteria = []
    for station in stations:
        if station.needs_correction == False:
            continue
        cont = 0
        for correlation in correlation_list:
            if np.isnan(correlation.t_app[-1]) == False:
                if correlation.station1_code == station.code:
                    cont += 1
                elif correlation.station2_code == station.code:
                    cont += 1
        if cont < min_no_corr:
            stations_that_dont_meet_criteria.append(station)

    return stations_that_dont_meet_criteria


def dates_of_correlations_with_t_app(cd, station_code):
    '''
    Method to retrieve all the dates of correlations where the t_app is not
    NaN.

    Returns
    -------
    None.

    '''

    correlations = correlations_of_station(cd.correlations, station_code)
    if len(correlations) == 0:
        return []
    avg_dates = []
    for c in correlations:
        if np.isnan(c.t_app[-1]) == False:
            date_excluding_hour = str(c.average_date)[:10]
            avg_dates.append(date_excluding_hour)

    return avg_dates


def dates_of_correlations_without_t_app(cd, station_code):
    '''
    Method to retrieve all the dates of correlations where the t_app is not
    NaN.

    Returns
    -------
    None.

    '''

    correlations = correlations_of_station(cd.correlations, station_code)
    avg_dates = []
    for c in correlations:
        if np.isnan(c.t_app[-1]):
            date_excluding_hour = str(c.average_date)[:10]
            avg_dates.append(date_excluding_hour)

    return avg_dates


def recount_correlations_removing_station(cd, station, remove_station,
                                          days_apart=60):
    '''
    Method to recount the number of correlations of each station and each
    correlation period after removing a station.

    Parameters
    ----------
    station_code : TYPE
        DESCRIPTION.

    Returns
    -------
    None.

    '''
    obs_t = obspy.UTCDateTime
    correlations = correlations_of_station(cd.correlations, station.code)
    if len(correlations) == 0:
        return []
    avg_dates = []
    for c in correlations:

        # If the apparent time was calculated
        if np.isnan(c.t_app[-1]) == False:

            # If the correlation is different from the one to remove.
            if (c.station1_code != remove_station and
                    c.station2_code != remove_station):
                date_excluding_hour = str(c.average_date)[:10]
                avg_dates.append(date_excluding_hour)

    avg_dates = sorted(avg_dates)
    if len(avg_dates) == 0:
        station.no_corr_per_avg_date = {"No available correlations": 0}
        return

    dates_unique = {avg_dates[0]: 1}
    for date in avg_dates[1:]:
        new_key = True
        # Check if date already in dictionary and if so then add to its count.
        if dates_unique.get(date) is not None:
            dates_unique[date] += 1
            new_key = False
            continue
        # Check if the difference between the dates and the date we are
        # checking is smaller than the days_apart argument given as an input.
        for date_counted in dates_unique.keys():
            if abs(obs_t(date_counted) - obs_t(date)) < days_apart * 86400:
                dates_unique[date_counted] += 1
                new_key = False
                break
        if new_key:
            dates_unique[date] = 1
    station.updated_no_corr_per_date = dates_unique


# Definitions of the classes ###############################################


class Processing_parameters():
    '''
    '''

    def __init__(self,
                 freqmin=0.15,    # Low freq. for the bandpass filter
                 freqmax=0.3,    # High freq. for the bandpass filter
                 ref_vel=2500,    # m/s
                 dist_trh=2.0,  # Min station separation in terms of wavelength
                 snr_trh=10,      # Signal-to-noise ratio threshold
                 noise_st=240,  # start of the noise window.
                 dt_err=0.004,  # Sampling interval must be multiple of this
                 # value.
                 resp_details=False):
        self.freqmin = float(freqmin)
        self.freqmax = float(freqmax)
        self.ref_vel = float(ref_vel)
        self.dist_trh = float(dist_trh)
        self.snr_trh = float(snr_trh)
        self.noise_st = noise_st
        self.dt_err = dt_err

    def __repr__(self):
        msg = "Processing Parameters Object:"
        msg += "\nMinimum frequency: " + str(self.freqmin)
        msg += "\nMaximum frequency: " + str(self.freqmax)
        msg += "\nReference surface wave velocity: " + str(self.ref_vel)
        msg += "\nMinimum station separation in terms of wavelength: "
        msg += str(self.dist_trh)
        msg += "\nSignal-to-noise ratio threshold: " + str(self.snr_trh)
        msg += "\nStart of the noise window: " + str(self.noise_st)
        return msg


class Station():
    '''
    '''

    def __init__(self, code, index, needs_correction, latitude, longitude,
                 elevation, sensor_type, project):
        '''
        Initialize the station object.

        Parameters
        ----------
        code : str
        needs_correction : bool
        latitude : float
        longitude : float
        elevation : float
        sensor_type : str
        project : str
        '''
        self.code = str(code)
        self.sensor_type = str(sensor_type)
        self.project = str(project)
        self.index = index
        self.included_in_inversion = True
        if needs_correction == 'True':
            self.needs_correction = True
        elif needs_correction == 'False':
            self.needs_correction = False
        else:
            msg = "Error with station " + self.code
            msg += "\nThe value for needs_correction should be True or False."
            raise Exception(msg)

        # Only stations that need correction will have a function describing
        # their clock drift. This function is f(t) = at + b.
        # We initialize both variables as lists because we will iteratively
        # calculate the a's and b's until reaching a stable solution.
        if needs_correction:
            self.a = []
            self.b = []

        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.elevation = float(elevation)

        # self.path2corrFile = path2corrFile

    def __repr__(self):
        info_string = ("\n Station object\n Code: " + self.code +
                       "\n Index: " + str(self.index) +
                       "\n Project: " + self.project +
                       "\n Sensor type: " + self.sensor_type +
                       "\n Needs correction: " + str(self.needs_correction) +
                       "\n Latitude: " + str(self.latitude) +
                       "\n Longitude: " + str(self.longitude) +
                       "\n Elevation: " + str(self.elevation) +
                       "\n a values: " + str(self.a) +
                       "\n b values: " + str(self.b))
        return info_string


class Correlation(object):
    '''
    '''

    def __init__(self, station1_code, station2_code, average_date, number_days,
                 file_path, npts, sampling_rate, length_of_file_s, delta,
                 cpl_dist, processing_parameters):
        self.station1_code = str(station1_code)
        self.station2_code = str(station2_code)
        self.average_date = obspy.UTCDateTime(average_date)
        self.number_days = float(number_days)

        if os.path.exists(file_path) == False:
            msg = "Directory with the correlation file couldn't be found: "
            raise Exception(msg)
        self.file_path = file_path
        self.npts = int(npts)
        self.sampling_rate = float(sampling_rate)
        self.length_of_file_s = float(length_of_file_s)
        self.delta = float(delta)
        self.cpl_dist = cpl_dist
        self.t_app = "Not calculated yet."
        self.apriori_dt1 = "Not calculated yet."
        self.apriori_dt2 = "Not calculated yet."
        self.dt_ins_station1 = "Not calculated yet."
        self.dt_ins_station2 = "Not calculated yet."
        self.processing_parameters = processing_parameters

    def __repr__(self):
        info_string = ("\n Correlation object" +
                       "\n Station 1: " + self.station1_code +
                       "\n Station 2: " + self.station2_code +
                       "\n Average date of CC: " + str(self.average_date) +
                       "\n Number of days: " + str(self.number_days) +
                       "\n Path: " + self.file_path +
                       "\n apriori_dt1: " + str(self.apriori_dt1) +
                       "\n apriori_dt2: " + str(self.apriori_dt2) +
                       "\n dt_ins_station1: " + str(self.dt_ins_station1) +
                       "\n dt_ins_station2: " + str(self.dt_ins_station2) +
                       "\n t_app: " + str(self.t_app) + "\n")
        return info_string

    def calculate_t_app(self, resp_details=False):
        '''
        It uses the last dt_ins estimate calculated for the station1 and
        station 2.

        Parameters
        ----------
        freqmin : TYPE
            DESCRIPTION.
        freqmax : TYPE
            DESCRIPTION.
        ref_vel : TYPE
            DESCRIPTION.
        dist_trh : TYPE
            DESCRIPTION.
        snr_trh : TYPE
            DESCRIPTION.
        noise_st : TYPE
            DESCRIPTION.
        dt_err : TYPE
            DESCRIPTION.
        resp_details : TYPE, optional
            DESCRIPTION. The default == False.
        '''
        freqmin = self.processing_parameters.freqmin
        freqmax = self.processing_parameters.freqmax
        ref_vel = self.processing_parameters.ref_vel
        dist_trh = self.processing_parameters.dist_trh
        snr_trh = self.processing_parameters.snr_trh
        noise_st = self.processing_parameters.noise_st
        dt_err = self.processing_parameters.dt_err

        # Set variables for locating the fortran codes and the parameter file.
        current_path = os.path.abspath(__file__)
        clock_errors_py_dir = (Path(current_path).parents[0])
        program_dir = os.path.join(clock_errors_py_dir,
                                   'recover_timing_errors-master')
        params_dir = os.path.join(program_dir, 'params.txt')
        xcorr_path = self.file_path

        results_dir_name = os.path.basename(self.file_path)
        results_dir_name.replace('.sac', '')
        results_dir_name.replace(self.station1_code + '-'
                                 + self.station2_code, '')

        # Station separation. Great circle distance in m using WGS84 ellipsoid.
        cpl_dist = self.cpl_dist
        min_wl = ref_vel / freqmax  # Minimum wavelength separation.

        if cpl_dist / min_wl < dist_trh:
            msg = 'Station couple does not exceed minimum separation'
            self.resp_details = msg
            if not isinstance(self.t_app, list):
                self.t_app = [np.nan]
            else:
                self.t_app.append(np.nan)
            return(np.nan, [msg, cpl_dist])
        try:
            # Apriori estimates of both stations.
            apr_dt_st1 = float(self.dt_ins_station1[-1])
            apr_dt_st2 = float(self.dt_ins_station2[-1])
        except Exception:
            msg = "No apriori estimate found for station 1 and station 2."
            raise Exception(msg)

        with open(params_dir, 'w') as file:
            msg = ("# data_dir, station_1, station_2, results_dir_name, "
                   "nsamples, freqmin, freqmax, cpl_dist, ref_vel, dist_trh, "
                   "snr_trh, noise_st, apr_dt_st1, apr_dt_st2, dt_err, "
                   "resp_details \n")
            file.write(msg)

            for val in [xcorr_path, self.station1_code, self.station2_code,
                        results_dir_name, self.npts, freqmin, freqmax,
                        cpl_dist, ref_vel, dist_trh, snr_trh, noise_st,
                        apr_dt_st1, apr_dt_st2, dt_err, resp_details]:
                file.write(str(val) + '\n')
        os.chdir(program_dir)

        result = subprocess.run(["./BIN/Timing_err_inv"], capture_output=True)
        errors = str(result.stderr).replace("b'", "").split('\\n')
        output = str(result.stdout).replace("b'", "").split('\\n')
        for a in errors:
            print(a)
        for a in output:
            print(a)
            if 'Result shift:' in a:
                shift = float(a.split(':')[1])
            elif 'Results saved in folder:' in a:
                folder_dir = a.split(':')[1]
            elif 'SNR causal wave' in a:
                snr_c = float(a.replace('SNR causal wave', ''))
                self.snr_c = snr_c
            elif 'SNR acausal wave' in a:
                snr_a = float(a.replace('SNR acausal wave', ''))
                self.snr_c = snr_a
            elif 'acausal signal until index:' in a:
                acausal_from_index = a.split(':')[1]
                self.acausal_from_index = acausal_from_index
            elif 'acausal signal from index:' in a:
                acausal_until_index = a.split(':')[1]
                self.acausal_until_index = acausal_until_index
            elif 'causal signal from index:' in a:
                causal_from_index = a.split(':')[1]
                self.causal_from_index = causal_from_index
            elif 'causal signal until index:' in a:
                acausal_until_index = a.split(':')[1]
                self.causal_until_index = acausal_until_index

        if ('shift' in locals()) == False:
            shift = np.nan
            folder_dir = output
        if resp_details:
            self.resp_details = folder_dir
        if not isinstance(self.t_app, list):
            self.t_app = [shift]
        else:
            self.t_app.append(shift)
        self.station_separation = cpl_dist
        # TODO: Calculate signal to noise raito.


class Clock_drift(object):
    '''
    '''

    def __init__(self, station_file, path2data_dir, reference_time,
                 list_of_processing_parameters):
        '''
        Parameters
        ----------
        station_file : str file_path
            Path to location of the station file that contains all the
            information of all the stations.
        path2data_dir : str folder_path
            Path to the folder that contains all the cross correlations.
        reference_time : str
            reference time or the date considered the zero time.
        processing_parameters : Processing_Parameters object
            DESCRIPTION.

        '''
        self.reference_time = obspy.UTCDateTime(reference_time)
        self.set_processing_parameters(list_of_processing_parameters)
        self.set_stations(station_file, path2data_dir)
        self.set_correlations(path2data_dir)
        self.path2data_dir = path2data_dir
        self.iteration = 0

    def __repr__(self):
        info_string = "Clock_drift object\n"
        info_string += "There are " + str(len(self.stations)) + " stations"
        info_string += "stored within the Clock_drift object. \n"
        info_string += "There are " + str(len(self.correlations))
        info_string += " correlations stored within the Clock_drift object. \n"
        return info_string

    def set_processing_parameters(self, list_of_processing_parameters):
        if not isinstance(list_of_processing_parameters, list):
            msg = "list of processing parameters should be a list containing"
            msg += " the different Processing Parameter objects."
            raise Exception(msg)
        for lst in list_of_processing_parameters:
            str_type = "<class 'oclock.Processing_parameters'>"
            if str(type(lst)) != str_type:
                msg = "The objects inside the list should be of the type "
                msg += "Processing_parameters"
                raise Exception(msg)
        self.processing_parameters = list_of_processing_parameters

    def set_correlations(self, path2data_dir):
        if os.path.exists(path2data_dir) == False:
            msg = "Directory with the correlation files couldn't be found: "
            msg += path2data_dir
            raise Exception(msg)
        correlations = []
        for file in sorted(os.listdir(path2data_dir)):
            # The file header contains the day in the middle of the correlation
            # averaged over the available days
            if '.sac' not in file:
                continue
            attributes = file.replace('.sac', '').split('_')
            station1_code = attributes[0]
            station2_code = attributes[1]
            try:
                station1 = self.get_station(station1_code)
                station2 = self.get_station(station2_code)
            except BaseException:
                msg = "Couldn´t find the stations of the file: "
                msg += str(file)
                msg += " in the inventory of the stations provided."
                msg += " If you want to use this file please add the stations"
                msg += " to the station_file."
                # print(msg)
                continue

            if (station1.needs_correction == False
                    and station2.needs_correction == False):
                continue

            file_path = os.path.join(path2data_dir, file)
            try:
                tr = read_correlation_file(file_path)
            except BaseException:
                msg = "Couldn´t open the file: "
                msg += str(file_path)
                msg += "\n Remember that it needs to have the format:"
                msg += "station1code_station2code_averageDate_noCorrelatedDays"
                msg += ".sac"
                print(msg)
                continue

            # Station separation.
            # Great circle distance in m using WGS84 ellipsoid.
            cpl_dist = gps2dist_azimuth(station1.latitude,
                                        station1.longitude,
                                        station2.latitude,
                                        station2.longitude)[0]
            average_date = tr.stats.average_date
            number_days = tr.stats.number_of_days
            npts = tr.stats.npts
            delta = tr.stats.sac.delta
            sampling_rate = tr.stats.sampling_rate
            length_of_file_s = tr.stats.endtime - tr.stats.starttime
            for processing_parameters in self.processing_parameters:
                correlation = Correlation(station1_code, station2_code,
                                          average_date, number_days,
                                          file_path, npts, sampling_rate,
                                          length_of_file_s, delta, cpl_dist,
                                          processing_parameters)
                t_N_lps = (average_date - self.reference_time) / 86400.0
                correlation.t_N_lps = t_N_lps
                correlations.append(correlation)
        self.correlations = correlations
        self.path2data_dir = path2data_dir

    def set_stations(self, station_file, path2data_dir):
        if os.path.exists(station_file) == False:
            msg = "Station file couldn't be found: " + station_file
            raise Exception(msg)

        stations = []
        with open(station_file) as file:
            rows = file.readlines()[1:]
            index = 0
            for row in rows:
                columns = row.split()
                project = columns[0]
                sensor_code = columns[1]
                needs_correction = columns[2]
                latitude = columns[3]
                longitude = columns[4]
                elevation = columns[5]
                elevation = 0 if elevation == '-' else elevation
                sensor_type = str(row.split()[6])
                if correlations_of_station_exist(sensor_code, path2data_dir):
                    sta = Station(code=sensor_code, index=index,
                                  needs_correction=needs_correction,
                                  latitude=latitude, longitude=longitude,
                                  elevation=elevation, sensor_type=sensor_type,
                                  project=project)
                    stations.append(sta)
                    index += 1
                else:
                    print("No correlation files found for station:" +
                          sensor_code)
        self.stations = stations
        self.station_names = [sta.code for sta in stations]

    def get_station(self, station_code):
        for station in self.stations:
            if station.code == station_code:
                return station

        msg = "Station not found"
        raise Exception(msg)

    def get_correlations_of_stationpair(self, station1_code, station2_code):
        get_correlations = []
        if station1_code == station2_code:
            msg = "You have to choose two different stations."
            raise Exception(msg)
        for correlation in self.correlations:
            if (station1_code != correlation.station1_code
                    and station1_code != correlation.station2_code):
                continue
            if (station2_code != correlation.station1_code
                    and station2_code != correlation.station2_code):
                continue
            get_correlations.append(correlation)
        return get_correlations

    def get_correlation_of_file(self, file_path):
        for correlation in self.correlations:
            if correlation.file_path == file_path:
                return correlation
        raise Exception("Correlation not found")

    def add_processing_parameters(self, new_processing_parameters,
                                  ask4verification=True):
        '''
        Method to add a new set of parameters.

        Parameters
        ----------
        processing_parameters : TYPE
            DESCRIPTION.

        Returns
        -------
        None.

        '''
        if ask4verification:
            a = input("The calculated time shifts for each cross-correlation"
                      "will be lost. The clock error functions (a and b)"
                      " of each station will still be stored. "
                      "Do you wish to continue? (y/n) /n")
            if a != "y" and a != "n":
                raise Exception("Answer should be y or n.")
            if a == "n":
                return
        list_of_processing_parameters = self.processing_parameters
        list_of_processing_parameters.append(new_processing_parameters)
        self.set_processing_parameters(list_of_processing_parameters)
        self.set_correlations(self.path2data_dir)
        for correlation in self.correlations:
            correlation.dt_ins_station1 = []
            correlation.dt_ins_station2 = []

    def calculate_aprioridt_4_allcorrelations(self):
        '''
        Function that calculates the apriori dt for all correlation files.
        Given the all the stations contained in the clock drift object, the
        function calculates all the possible station-pair combinations
        and then calculates the apriori estimate for each of the correlations.

        When using different processing parameters, we check if the apriori
        estimate is the same, if it is not then one of the two is wrong
        so we will use the value of 0 as an apriori estimate.
        Returns
        -------
        None.
        '''
        # for correlation in self.correlations:
        #     correlation.apriori_dt1 = 0
        #     correlation.apriori_dt2 = 0
        print("Calculating the apriori estimates for each stationpair")
        for i, station1 in enumerate(self.stations):
            for station2 in self.stations[i + 1:]:  # Avoids repeating stantns.
                sta1 = station1
                sta2 = station2
                correlations = self.get_correlations_of_stationpair(sta1.code,
                                                                    sta2.code)
                if len(correlations) == 0:
                    continue
                for params in self.processing_parameters:
                    correlations_params = correlations_with_parameters(
                        correlations, params)
                    # If there are no corelations for station pair... skip
                    if len(correlations_params) == 0:
                        continue

                    # If there is only one corelation assume the first
                    # estimate as 0 time shift.
                    if len(correlations_params) == 1:
                        correlations_params[0].apriori_dt1 = 0
                        correlations_params[0].apriori_dt2 = 0
                        continue
                    # Else calculate the apriori estimate.
                    calculate_apriori_dt(self, correlations_params)

    def calculate_aprioridt_4_allcorrelations_corrected(self):
        '''
        Function that calculates the apriori dt for all correlation files.
        Given the all the stations contained in the clock drift object, the
        function calculates all the possible station-pair combinations
        and then calculates the apriori estimate for each of the correlations.

        When using different processing parameters, we check if the apriori
        estimate is the same, if it is not then one of the two is wrong
        so we will use the value of 0 as an apriori estimate.
        Returns
        -------
        None.

        '''
        # for correlation in self.correlations:
        #     correlation.apriori_dt1 = 0
        #     correlation.apriori_dt2 = 0
        print("Calculating the apriori estimates for each stationpair")
        for i, station1 in enumerate(self.stations):
            for station2 in self.stations[i + 1:]:  # Avoids repeatingstations.
                sta1 = station1
                sta2 = station2
                correlations = self.get_correlations_of_stationpair(sta1.code,
                                                                    sta2.code)
                if len(correlations) == 0:
                    continue
                for params in self.processing_parameters:
                    correlations_params = correlations_with_parameters(
                        correlations, params)
                    # If there are no corelations for station pair... skip
                    if len(correlations_params) == 0:
                        continue

                    # If there is only one corelation assume the first
                    # estimate as 0 time shift.
                    if len(correlations_params) == 1:
                        correlations_params[0].apriori_dt1 = 0
                        correlations_params[0].apriori_dt2 = 0
                        continue
                    # Else calculate the apriori estimate.
                    calculate_apriori_dt_corrected(self, correlations_params)

    def calculate_dt_ins(self):
        '''
        Calculate the dt_ins estimates for each of the correlation files.
        Each pariori estimate has a value for the a-causal wave (station 1)
        and a value for the causal wave (station 2).

        To calculate the dt_ins estimates we use the a and b value of both
        stations.
        First, we check if the first dt_ins estimate exists, otherwise it
        gets calculated.
        Then, we retrieve all the cross-correlations present in the clock
        drift object.
        After, if there are previously calculated a and b values for the
        stations we calculate the t_N_lps (look at Naranjo et al.,
        2021).
        Otherwise, we use the apriori estimates.
        If the station doesn't need correction, the dt_ins estimate will be 0.
        The dt_ins estimates will be save in a list so that we can check how
        they evolve with each iteration.

        Parameters
        ----------

        Returns
        -------
        None.

        '''
        # Check if the apriori estimate was already calculated. Except:
        for c in self.correlations:
            if c.apriori_dt1 == "Not calculated yet.":
                self.calculate_aprioridt_4_allcorrelations()
        #  calculate it.
        # for sta in self.stations:
        #     if sta.needs_correction:
        #         if len(sta.a)==0 or len(sta.b) == 0:
        #             print("Calculating the apriori estimates for each "
        #                   "stationpair.")
        #             self.calculate_aprioridt_4_allcorrelations()
        # TODO: fix this check.
        for correlation in self.correlations:
            station1 = self.get_station(correlation.station1_code)
            station2 = self.get_station(correlation.station2_code)
            t_N_lps = correlation.t_N_lps
            if station1.needs_correction:
                # If there is an a value or b value for the station
                # use them for calculating the dt_ins estimates.
                if len(station1.a) > 0 and len(station1.b) > 0:
                    # Take the last calculated a_value and b_value
                    a_val = station1.a[-1]
                    b_val = station1.b[-1]
                    dt_ins_station1 = a_val * t_N_lps + b_val
                    correlation.dt_ins_station1.append(dt_ins_station1)
                # If there are no a or b values then use the apriori
                # estimate as the t_app.
                else:
                    correlation.dt_ins_station1 = [correlation.apriori_dt1]
            # If the station doesn't need correction then the a=b=0
            else:
                if len(station1.a) > 0:
                    correlation.dt_ins_station1.append(0)
                else:
                    correlation.dt_ins_station1 = [0]
            if station2.needs_correction:
                if len(station2.a) > 0:
                    # Take the last calculated a_value and b_value
                    a_val = station2.a[-1]
                    b_val = station2.b[-1]
                    dt_ins_station2 = a_val * t_N_lps + b_val
                    correlation.dt_ins_station2.append(dt_ins_station2)
                else:
                    correlation.dt_ins_station2 = [correlation.apriori_dt2]
            else:
                if len(station2.a) > 0:
                    correlation.dt_ins_station2.append(0)
                else:
                    correlation.dt_ins_station2 = [0]

    def calculate_tapp_4_allcorrelations(self, days_apart=50):
        '''
        First it calculates the observed shift or t_app. Then it adds an
        atribute to the stations containing how many correlations
        the station has per correlation period. The correlation period
        is determined by the attribute days_apart which determines
        how far the date of two correlations should be to be considered one
        period.

        Returns
        -------
        Results are saved inside the correlation objects and station objects.
        '''

        # Check if the correlation still has the default values.
        for c in self.correlations:
            if (isinstance(c.dt_ins_station1, str)
                    or isinstance(c.dt_ins_station2, str)):

                self.calculate_dt_ins()

        print("Calculating the t_app for each stationpair.")
        with suppress_stdout():
            for correlation in self.correlations:
                correlation.calculate_t_app()

        # Add an atribute to the stations containing how many correlations
        # the station has per correlation period. The correlation period
        # is determined by the attribute days_apart which determines
        # how far the date of two correlations should be to be considered one
        # period.
        for station in self.stations:
            self.no_corr_per_avg_date(station, days_apart=days_apart,
                                      plot=False)

    def stations_with_few_corrs(self, min_no_corr_periods,
                                min_no_correlations4obs,
                                min_no_correlations4land, days_apart):
        '''
        If station has the required number of correlations for a
        minimum_number_of correlation periods then the attribute
        "included_in_inversion" will be True.
        If the station does not fit the criteria then it will not be
        included in the inversion and all the correlations will be recounted
        after removing thecorrelations from this station.

        Parameters
        ----------
        stations : TYPE
            DESCRIPTION.

        Returns
        -------
        None.
        '''

        for station in self.stations:
            station.included_in_inversion = True

        restart = True
        while restart:
            restart = False
            for station in self.stations:

                # The threshold is different for land stations than obs so we set the
                # minimum values depending on this parameter.
                if station.needs_correction:
                    min_no_correlations = min_no_correlations4obs

                else:
                    min_no_correlations = min_no_correlations4land

                # After several loops there are some station that dont meet the
                # and were already removed, so continue if this is the case.
                if station.included_in_inversion == False:
                    continue

                if hasattr(station, 'updated_no_corr_per_date') == False:
                    station.updated_no_corr_per_date = station.no_corr_per_avg_date

                no_corr_per_avg_date = station.updated_no_corr_per_date

                # Count how many correlation periods meet the criteria for
                # this station.
                no_corr_periods = 0
                for date in no_corr_per_avg_date.keys():
                    if no_corr_per_avg_date[date] >= min_no_correlations:
                        no_corr_periods += 1

                # If the station doesnt meet the criteria the remove it.
                if no_corr_periods < min_no_corr_periods:
                    station.included_in_inversion = False
                    restart = True
                    for temp_station in self.stations:
                        if temp_station.included_in_inversion:
                            recount_correlations_removing_station(self,
                                                                  temp_station,
                                                                  remove_station=station.code,
                                                                  days_apart=days_apart)

                    break
                else:
                    station.included_in_inversion = True

    def build_matrices(self):
        '''
        Method to build the matrices and vector mentioned in the paper
        Naranjo et al., (2021)
        min_no_corr are the minimum number of cross-correlations that a
        station should have to be included in the inversion.
        Returns
        -------
        None.

        '''
        # Check first if the apparent times were calculated for all the
        # correlations
        try:
            [float(correlation.t_app[-1]) for correlation in self.correlations]
        except BaseException:
            self.calculate_tapp_4_allcorrelations()
        A = []  # Matrix A shown in the paper Weemstra, Naranjo et al., (2021)
        vector_tapp = []
        station1_codes, station2_codes = [], []
        average_date, cpl_distances, number_of_days = [], [], []
        estimated_shifts = []
        # There are two unkowns per station (a and b).
        n = len(self.stations) * 2

        correlation_list = self.correlations
        for params in self.processing_parameters:
            correlations = correlations_with_parameters(correlation_list,
                                                        params)
            for correlation in correlations:

                # Get the stations of the cross-correlation file.
                station1 = self.get_station(correlation.station1_code)
                station2 = self.get_station(correlation.station2_code)
                # TODO:
                if station1.included_in_inversion == False:
                    continue
                if station2.included_in_inversion == False:
                    continue
                t_N_lps = correlation.t_N_lps
                t_app = correlation.t_app[-1]

                # If the apparent time is nan the skip.
                if np.isnan(t_app) == True:
                    continue

                # If it is the first inversion then we cant compute the
                # estimated shift
                if len(correlation.t_app) == 1:
                    estimated_shift = np.nan
                else:
                    # print(correlation)
                    estimated_shift = self.calculate_estimated_shift(
                        correlation)
                # Make the row to append to matrix A.
                a = np.zeros(n)
                if station1.needs_correction:
                    a[station1.index * 2:station1.index * 2 + 2] = 2
                    a[station1.index * 2] = 2 * t_N_lps
                if station2.needs_correction:
                    a[station2.index * 2:station2.index * 2 + 2] = -2
                    a[station2.index * 2] = -2 * t_N_lps

                # Store the information of the correlation with shift.
                A.append(a)
                vector_tapp.append(t_app)
                station1_codes.append(correlation.station1_code)
                station2_codes.append(correlation.station2_code)
                average_date.append(correlation.average_date)
                cpl_distances.append(correlation.cpl_dist)
                number_of_days.append(correlation.number_days)
                estimated_shifts.append(estimated_shift)
        # Make the matrix and vector asarray.
        A = np.asarray(A)
        vector_tapp = np.asarray(vector_tapp)

        # We can create a dataframe with all the information.
        df = pd.DataFrame(list(zip(station1_codes, station2_codes, average_date,
                                   vector_tapp, estimated_shifts,
                                   (estimated_shifts - vector_tapp),
                                   cpl_distances, number_of_days)),
                          columns=['Station1', 'Station2', 'average_date',
                                   't_app[s]', 'estimated_shifts',
                                   'estimated-observed',
                                   'Station_separation[m]',
                                   'Number_of_days_correlated'])

        # Then we save matrix A as a dataframe with clear headers.
        column_names = []

        # Station pairs is a list with the station pairs of each correlation
        # pair that was used to build the matrix. This will be used in the
        # dataframe.
        station_pairs = [sta1 + "_" + sta2 for sta1, sta2 in zip(station1_codes,
                                                                 station2_codes)]

        # Make the header for the matrix A to make it easier to check.
        for i in range(n):
            for station in self.stations:
                if station.index == i:
                    sta_code = station.code
                    column_names.append("a*t_{N_lps} (" + sta_code + ")")
                    column_names.append("b (" + sta_code + ")")
                    break
        matrix_A = pd.DataFrame(A, columns=column_names, index=station_pairs)
        #  We remove columns that only contain zeros
        matrix_A = matrix_A.loc[:, (matrix_A != 0).any(axis=0)]
        self.matrix_A = matrix_A
        self.df = df
        # Check if there are stations without apparent times.
        for sta in self.stations:
            if sta.needs_correction == True:
                found = False
                for name in matrix_A.columns:
                    if sta.code in name:
                        found = True
                        break
                if found == False:
                    print("No t_app found for Station: " + sta.code)

    def solve_eq(self, rcond=None):
        '''
        It inverts the matrix and creates a dataframe containing the
        stations with solutions.

        If after the inversion some stations didnt have a solution (e.g
        insufficient cross-correlations) it will assign a correction of zero.
        Meaning than in the worst case scenario the data will stay the same as
        at the beginning

        Returns
        -------
        None.

        '''
        try:
            A_dum = self.matrix_A.copy()
            Tobs_dum = self.df['t_app[s]'].copy()
        except BaseException:
            self.build_matrices()
            A_dum = self.matrix_A.copy()
            Tobs_dum = self.df['t_app[s]'].copy()

        print("Inverting the matrix and calculating a and b for each station.")
        x = np.linalg.lstsq(A_dum, Tobs_dum, rcond=rcond)[0]

        column_names = [i.replace('*t_{N_lps}', '')
                        for i in self.matrix_A.columns]
        sol = pd.DataFrame(columns=column_names)
        sol.loc['values'] = x

        # This list will be used to verify that all stations have solutions.
        stations_with_solutions = []
        for value, header in zip(x, column_names):
            if "a" in header:
                station_code = header.replace("a (", "").replace(")", "")
                stations_with_solutions.append(station_code)
                station = self.get_station(station_code)
                station.a.append(value)
                continue
            if "b" in header:
                station_code = header.replace("b (", "").replace(")", "")
                station = self.get_station(station_code)
                station.b.append(value)
        self.solution = sol
        self.iteration += 1

        # Make the correction be equal to zero for stations without
        # measurements.
        for station in self.stations:
            if station.needs_correction:
                if station.code not in stations_with_solutions:
                    station.a.append(0)
                    station.b.append(0)
    # 3
    # TODO: Add next step iteration to double check that steps are not repeated.
    ##########################################################################

    def calculate_estimated_shift(self, correlation, iteration=-1):
        '''
        Method to calculate the estimated time shift using the a and b values
        of a given station.

        Parameters
        ----------
        correlation : TYPE
            DESCRIPTION.
        iteration : TYPE, optional
            DESCRIPTION. The default is -1.

        Returns
        -------
        estimate : TYPE
            DESCRIPTION.

        '''
        station1 = self.get_station(correlation.station1_code)
        station2 = self.get_station(correlation.station2_code)
        a_val_sta1, a_val_sta2 = 0, 0
        b_val_sta1, b_val_sta2 = 0, 0
        if station1.needs_correction:
            a_val_sta1 = float(station1.a[iteration])
            b_val_sta1 = float(station1.b[iteration])

        if station2.needs_correction:
            a_val_sta2 = float(station2.a[iteration])
            b_val_sta2 = float(station2.b[iteration])
        dt_ins_station1 = (a_val_sta1 * correlation.t_N_lps + b_val_sta1)
        dt_ins_station2 = a_val_sta2 * correlation.t_N_lps + b_val_sta2
        estimate = 2 * (dt_ins_station1 - dt_ins_station2)
        correlation.estimated_shift = estimate
        return estimate

    def no_corr_per_avg_date(self, station, days_apart=60, plot=True):
        '''
        Function to calculated how many shifts could be observed of the
        different cross-correlations of a given station.

        Parameters
        ----------
        station_code : TYPE
            DESCRIPTION.
        days_apart : int
            How many days apart should the function consider to be the same
            correlation period
        plot : TYPE, optional
            DESCRIPTION. The default is True.

        Returns
        -------
        Adds attribute to the stations that keeps track of how many stations have
        observed time shifts.
        The results are saved in station.no_corr_per_avg_date

        '''
        obs_t = obspy.UTCDateTime
        avg_dates = sorted(
            dates_of_correlations_with_t_app(
                self, station.code))
        if len(avg_dates) == 0:
            station.no_corr_per_avg_date = {"No available correlations": 0}
            return
        dates_unique = {avg_dates[0]: 1}
        for date in avg_dates[1:]:
            new_key = True
            # Check if date already in dictionary and if so then add to its
            # count.
            if dates_unique.get(date) is not None:
                dates_unique[date] += 1
                new_key = False
                continue
            # Check if the difference between the dates and the date we are checking
            # is smaller than the days_apart argument given as an input.
            for date_counted in dates_unique.keys():
                if abs(obs_t(date_counted) - obs_t(date)) < days_apart * 86400:
                    dates_unique[date_counted] += 1
                    new_key = False
                    break
            if new_key:
                dates_unique[date] = 1
        station.no_corr_per_avg_date = dates_unique

        # #################################3 Plot results#######################
        if plot:
            # Calculate correlations without t_app
            avg_dates_witout_t_app = (
                sorted(dates_of_correlations_without_t_app(self, station.code)))

            dates_witout_t_app = {avg_dates_witout_t_app[0]: 1}
            for date in avg_dates_witout_t_app[1:]:
                new_key = True
                # Check if date already in dictionary and if so then add to
                # its count.
                if dates_witout_t_app.get(date) is not None:
                    dates_witout_t_app[date] += 1
                    new_key = False
                    continue
                # Check if the difference between the dates and the date we
                # are checking is smaller than the days_apart argument given
                # as an input.
                for date_counted in dates_witout_t_app.keys():
                    if abs(obs_t(date_counted) - obs_t(date)
                           ) < days_apart * 86400:
                        dates_witout_t_app[date_counted] += 1
                        new_key = False
                        break
                if new_key:
                    dates_witout_t_app[date] = 1

            w = 0.15
            X = np.arange(len(dates_witout_t_app.keys()))
            plt.figure(dpi=300)
            plt.bar(
                X - w,
                dates_unique.values(),
                label="Dates with t_app",
                width=0.25)
            plt.bar(
                X + w,
                dates_witout_t_app.values(),
                label="Dates without t_app",
                width=0.25)
            plt.xticks(X, list(dates_witout_t_app.keys()))
            plt.title(station.code)
            plt.xlabel("Average date")
            plt.ylabel("Number of cross-correlations")
            plt.legend()
            plt.show()

    def plot_correlations_of_stationpair(self, station1_code, station2_code,
                                         iteration=-1, min_t=-50,
                                         max_t=30):
        '''
        Parameters
        ----------
        station1_code : TYPE
            DESCRIPTION.
        station2_code : TYPE
            DESCRIPTION.
        min_t : TYPE, optional
            DESCRIPTION. The default is -50.
        max_t : TYPE, optional
            DESCRIPTION. The default is 30.

        Returns
        -------
        None.
        '''

        correlation_list = self.get_correlations_of_stationpair(station1_code,
                                                                station2_code)
        # station1 = self.get_station(correlation_list[0].station1_code)
        # station2 = self.get_station(correlation_list[0].station2_code)

        # ref_t = self.reference_time

        # As some traces were processed differently we will separate the plots
        # into the groups of traces that share the same processing parameters.
        for params in self.processing_parameters:
            correlations = correlations_with_parameters(correlation_list,
                                                        params)

            f, ax1 = plt.subplots(1, 1, sharey=True, dpi=300)

            for correlation in correlations:
                # average_date = correlation.average_date
                freqmin = correlation.processing_parameters.freqmin
                freqmax = correlation.processing_parameters.freqmax
                tr = read_correlation_file(correlation.file_path)
                t1, data = trim_correlation_trace(
                    tr, min_t, max_t, freqmin, freqmax)
                ax1.plot(
                    t1, data, label=str(
                        tr.stats.average_date)[
                        :10], alpha=0.7)

            ax1.set_title(
                'Correlations of station pair: ' +
                tr.stats.station_pair)
            ax1.set_xlabel('Time [s]')
            ax1.set_ylabel('Amplitudes')
            ax1.legend(loc=2)
            plt.tight_layout()
            plt.show()

    def plot_before_n_after_apriori_estimation(self,
                                               station1_code,
                                               station2_code,
                                               min_t=-40,
                                               max_t=30,
                                               index_processing_parameters=0):
        '''
        Function to generate plot of the cross-correlations before and after
        applying the correction using the apriori estimate function.

        Parameters
        ----------
        station1_code : TYPE
            DESCRIPTION.
        station2_code : TYPE
            DESCRIPTION.
        index_processing_parameters : int
            As processing parameters is a list, oyu need to specify which of
            the processing parameters you need to use for plotting.

        Returns
        -------
        None.
        '''

        freqmin = self.processing_parameters[index_processing_parameters].freqmin
        freqmax = self.processing_parameters[index_processing_parameters].freqmax
        correlations = self.get_correlations_of_stationpair(station1_code,
                                                            station2_code)
        f, (ax1, ax2) = plt.subplots(2, 1, sharey=True, dpi=300)
        f.suptitle('Before and after apriori estimation')
        for correlation in correlations:
            tr = read_correlation_file(correlation.file_path)
            try:
                apriori_dt1 = float(correlation.apriori_dt1)
                apriori_dt2 = float(correlation.apriori_dt2)
                time_shift = apriori_dt1 + apriori_dt2
            except BaseException:
                msg = "You need to calculate the apriori estimates "
                msg += "before running this function."
                raise Exception(msg)
            t1, data = trim_correlation_trace(
                tr, min_t, max_t, freqmin, freqmax)
            ax1.plot(t1, data, label=str(tr.stats.average_date)[:10])
            ax2.plot(
                t1 + time_shift,
                data,
                label=str(
                    tr.stats.average_date)[
                    :10])

        ax1.set_title('Before correction ' + tr.stats.station_pair +
                      "\n time shift from first to lastc  correlation file = "
                      + str(time_shift))
        ax2.set_title('After correction ' + tr.stats.station_pair)
        ax2.set_xlabel('Time [s]')
        ax2.set_ylabel('Amplitudes')
        ax1.set_ylabel('Amplitudes')
        ax1.legend(loc=2)
        ax2.legend(loc=2)
        plt.tight_layout()
        plt.show()

    def plot_before_n_after_t_app(self,
                                  station1_code,
                                  station2_code,
                                  min_t=-40,
                                  max_t=30,
                                  index_processing_parameters=0):
        '''
        Function to generate plot of the cross-correlations before and after
        applying the correction using the t_app.

        Parameters
        ----------
        station1_code : TYPE
            DESCRIPTION.
        station2_code : TYPE
            DESCRIPTION.
        index_processing_parameters : int
            As processing parameters is a list, oyu need to specify which of
            the processing parameters you need to use for plotting.

        Returns
        -------
        None.

        '''
        freqmin = self.processing_parameters[index_processing_parameters].freqmin
        freqmax = self.processing_parameters[index_processing_parameters].freqmax
        correlations = self.get_correlations_of_stationpair(station1_code,
                                                            station2_code)
        f, (ax1, ax2) = plt.subplots(2, 1, sharey=True, dpi=300)
        f.suptitle('Before and after apriori estimation')
        for correlation in correlations:
            tr = read_correlation_file(correlation.file_path)
            try:
                time_shift = float(correlation.t_app[-1]) / 2
            except BaseException:
                msg = "You need to calculate the t_app "
                msg += "before running this function."
                raise Exception(msg)
            t1, data = trim_correlation_trace(
                tr, min_t, max_t, freqmin, freqmax)
            ax1.plot(t1, data, label=str(tr.stats.average_date)[:10])
            ax2.plot(
                t1 + time_shift,
                data,
                label=str(
                    tr.stats.average_date)[
                    :10])

        ax1.set_title('Before correction ' + tr.stats.station_pair +
                      "\n time shift from first to lastc  correlation file = "
                      + str(time_shift))
        ax2.set_title('After t_app estimation ' + tr.stats.station_pair)
        ax2.set_xlabel('Time [s]')
        ax2.set_ylabel('Amplitudes')
        ax1.set_ylabel('Amplitudes')
        ax1.legend(loc=2)
        ax2.legend(loc=2)
        plt.tight_layout()
        plt.show()

    def plot_all_tappValues_and_solution_of_station(self, station_code):
        '''
        Parameters
        ----------
        station_code : TYPE
            DESCRIPTION.

        Returns
        -------
        None.

        '''
        station = self.get_station(station_code)
        if station.included_in_inversion == False:
            return("This station was not included in the inversion because of" +
                   "the number of correlations it had.")
        # Take the last a value that has been calculated.
        a_val = float(station.a[-1])
        # Take the last a value that has been calculated.
        b_val = float(station.b[-1])
        ref_t = self.reference_time
        plt.figure(dpi=300)
        all_dates = []
        for station in self.stations:
            t_apps = []
            dates = []

            if station.code == station_code:
                continue
            color = 'royalblue'
            if station.needs_correction:
                color = 'grey'
            station2_code = station.code
            correlations = self.get_correlations_of_stationpair(station_code,
                                                                station2_code)
            for correlation in correlations:
                t_app = float(correlation.t_app[-1])
                if correlation.station1_code == station_code:
                    t_apps.append(t_app)
                else:
                    t_apps.append(-t_app)

                avg_date = correlation.average_date
                days_from_ref = (avg_date - ref_t) / 86400.0
                dates.append(days_from_ref)
                all_dates.append(days_from_ref)

            plt.scatter(dates, t_apps, color=color, edgecolor='k', zorder=99,
                        alpha=0.5)
            plt.plot(dates, t_apps, color=color, alpha=0.9)

        plt.plot(all_dates, np.asarray(all_dates) * a_val + b_val,
                 label='Solution', color='red', alpha=1, zorder=999,
                 linewidth=2)
        # label='f(t)=at+b\na: ' + str(a_val)[:6] + '\n b: ' + str(b_val)[:6],

        plt.scatter([], [], color='grey', label='correlated with OBS')
        plt.scatter(
            [],
            [],
            color='royalblue',
            label='correlated with land station')
        plt.title('Station: ' + station_code)
        plt.xlabel('Days from ' + str(ref_t)[:10])
        plt.ylabel('Clock differences [s]')
        plt.annotate(str(min(all_dates) * a_val + b_val)[:5],
                     (min(all_dates), min(all_dates) * a_val + b_val))
        plt.annotate(str(max(all_dates) * a_val + b_val)[:5],
                     (max(all_dates), max(all_dates) * a_val + b_val))
        plt.legend()
        plt.show()

    def plot_variation_of_tapp_per_iteration(self, station_code):
        '''
        Parameters
        ----------
        station_code : TYPE
            DESCRIPTION.

        Returns
        -------
        None.

        '''
        station = self.get_station(station_code)

        plt.figure(dpi=300)
        for station in self.stations:
            if station.code == station_code:
                continue

            station2_code = station.code
            correlations = self.get_correlations_of_stationpair(station_code,
                                                                station2_code)
            for correlation in correlations:
                t_apps = correlation.t_app
                iterations = np.linspace(0, len(t_apps) - 1, len(t_apps))
                plt.scatter(iterations, t_apps, color='black', edgecolor='k', zorder=99,
                            alpha=0.5, marker='+')
                plt.plot(
                    iterations,
                    t_apps,
                    color='red',
                    alpha=1,
                    linewidth=0.2)

        plt.title('Station: ' + station_code)
        plt.xlabel('Iteration')
        plt.ylabel('t_app [s]')
        plt.xticks(iterations)
        plt.legend()
        plt.show()

    def plot_correlation_beforeNafter_correction(self, station1_code, station2_code,
                                                 iteration=-1, min_t=-50,
                                                 max_t=30):
        '''
        Parameters
        ----------
        station1_code : TYPE
            DESCRIPTION.
        station2_code : TYPE
            DESCRIPTION.
        iteration : TYPE, optional
            DESCRIPTION. The default is -1.
        min_t : TYPE, optional
            DESCRIPTION. The default is -50.
        max_t : TYPE, optional
            DESCRIPTION. The default is 30.

        Returns
        -------
        None.

        '''
        correlation_list = self.get_correlations_of_stationpair(station1_code,
                                                                station2_code)
        station1 = self.get_station(correlation_list[0].station1_code)
        station2 = self.get_station(correlation_list[0].station2_code)
        if station1.needs_correction == False:
            if station2.needs_correction == False:
                raise Exception("Neither station needs correction.")

            if len(station2.a) == 0:
                raise Exception(station2.code +
                                " station has no correction yet.")
        elif len(station1.a) == 0:
            raise Exception(station1.code +
                            " station has no correction yet.")
        ref_t = self.reference_time

        # As some traces were processed differently we will separate the plots
        # into the groups of traces that share the same processing parameters.
        for params in self.processing_parameters:
            correlations = correlations_with_parameters(correlation_list,
                                                        params)

            f, (ax1, ax2) = plt.subplots(2, 1, sharey=True, dpi=300)

            for correlation in correlations:
                average_date = correlation.average_date
                t_N_lps = (average_date - ref_t) / 86400.0
                if station1.needs_correction:
                    correction_sta1 = -(station1.a[iteration] * t_N_lps +
                                        station1.b[iteration])
                else:
                    correction_sta1 = 0
                if station2.needs_correction:
                    correction_sta2 = (station2.a[iteration] * t_N_lps +
                                       station2.b[iteration])
                else:
                    correction_sta2 = 0
                shift = correction_sta1 + correction_sta2
                freqmin = correlation.processing_parameters.freqmin
                freqmax = correlation.processing_parameters.freqmax
                tr = read_correlation_file(correlation.file_path)
                t1, data = trim_correlation_trace(
                    tr, min_t, max_t, freqmin, freqmax)
                ax1.plot(
                    t1, data, label=str(
                        tr.stats.average_date)[
                        :10], alpha=0.7)
                ax2.plot(t1 + shift, data, label=str(tr.stats.average_date)[:10],
                         alpha=0.7)

            f.suptitle('Correction applied before and after inversion no: ' +
                       str(iteration))
            ax1.set_title('Before correction ' + tr.stats.station_pair)
            ax2.set_title('After correction ' + tr.stats.station_pair)
            ax2.set_xlabel('Time [s]')
            ax2.set_ylabel('Amplitudes')
            ax1.set_ylabel('Amplitudes')
            ax1.legend(loc=2)
            ax2.legend(loc=2)
            plt.tight_layout()
            plt.show()

    def plot_fluctuation_of_a_and_b(self):
        fig, (ax1, ax2) = plt.subplots(2, 1, dpi=300)
        for station in self.stations:
            # station = self.get_station(station_code)
            a_values = station.a
            b_values = station.b

            iterations = np.linspace(0, len(a_values) - 1, len(a_values))
            ax1.scatter(iterations, a_values, color='red', edgecolor='k', zorder=99,
                        alpha=0.5, marker='+')
            ax1.plot(iterations, a_values, color='red', alpha=1, linewidth=0.2)
            ax2.scatter(iterations, b_values, color='blue', edgecolor='k', zorder=99,
                        alpha=0.5, marker='+')
            ax2.plot(
                iterations,
                b_values,
                color='blue',
                alpha=1,
                linewidth=0.2)
        ax1.set_title('Fluctuation of a values')
        ax2.set_title('Fluctuation of b values')
        plt.xlabel('Iteration')
        plt.ylabel('a value')
        plt.legend()
        plt.tight_layout()
        plt.show()

    def plot_hist_no_correlations_per_station(self):
        '''
        Returns
        -------
        None.

        '''
        no_correlations_obs = []
        no_correlations_land = []
        station_codes_obs = []
        station_codes_land = []
        for station in self.stations:
            station_code = station.code
            station_count = 0
            for correlation in self.correlations:
                # TODO: This fix was added to avoid included a fake number of
                # correlations.
                if station.included_in_inversion == False:
                    continue

                # If the station is not in the correlation then continue to next
                # correlation.
                if correlation.station1_code != station_code:
                    if correlation.station2_code != station_code:
                        continue
                last_tapp = correlation.t_app[-1]
                if isinstance(last_tapp, str):
                    continue
                if np.isnan(last_tapp):
                    continue
                station_count += 1
            if station.needs_correction:
                no_correlations_obs.append(station_count)
                station_codes_obs.append(station.code)
                continue
            no_correlations_land.append(station_count)
            station_codes_land.append(station.code)

        fig, (ax1, ax2) = plt.subplots(2, 1, dpi=300)
        ax1.bar(station_codes_land, no_correlations_land,
                width=0.8, alpha=0.5,
                facecolor='C1', edgecolor='black', linewidth=1,
                align='center',)
        ax2.bar(station_codes_obs, no_correlations_obs,
                width=0.8, alpha=0.5,
                facecolor='C0', edgecolor='black', linewidth=1.5,
                align='center',)

        for ax in fig.axes:
            plt.sca(ax)
            plt.xticks(rotation=90)
        plt.xlabel("Station code")
        ax1.set_ylabel("Number of correlations")
        ax2.set_ylabel("Number of correlations")
        ax1.set_title("Number of cross-correlations of each station")
        plt.tight_layout()
        plt.show()

    def plot_inventory_correlations(self):
        '''
        Parameters
        ----------
        cd : TYPE
            DESCRIPTION.
        Returns
        -------
        None.

        '''
        fig, ax = plt.subplots(1, 1, dpi=300)
        no_station_connections = []
        for station in self.stations:
            station_code = station.code
            station_count = 0
            for correlation in self.correlations:
                # If it is the first iteration then count all the available
                # cross-correlations.
                if self.iteration != 0:
                    # If it is no longer the 0 iteration then show only the
                    # cross-correlations that were included in the inversion.
                    if station.included_in_inversion == False:
                        continue
                    if isinstance(correlation.t_app[-1], str):
                        continue
                    if np.isnan(correlation.t_app[-1]):
                        continue

                # If the station is not in the correlation then continue to next
                # correlation.
                if correlation.station1_code != station_code:
                    if correlation.station2_code != station_code:
                        continue

                station_count += 1
                sta1_temp = self.get_station(correlation.station1_code)
                sta2_temp = self.get_station(correlation.station2_code)
                ax.plot([sta1_temp.longitude, sta2_temp.longitude],
                        [sta1_temp.latitude, sta2_temp.latitude],
                        color='k', alpha=0.1, linewidth=0.4)
            no_station_connections.append(station_count)
            if station.needs_correction:
                ax.annotate(station_code + "\n" + str(station_count),
                            [station.longitude, station.latitude],
                            zorder=9999, alpha=0.8,
                            path_effects=[pe.withStroke(linewidth=4,
                                                        foreground="white")])
        lats = [sta.latitude for sta in self.stations]
        lons = [sta.longitude for sta in self.stations]
        colors = ['C0' if station.needs_correction else 'C1' for station in
                  self.stations]
        ax.scatter(lons, lats, c=colors, zorder=999,
                   edgecolor='k', s=no_station_connections,
                   alpha=0.7)
        ax.scatter([], [], label='OBS station', color='C0', edgecolor='k')
        ax.scatter([], [], label='Land station', color='C1', edgecolor='k')
        plt.legend(loc=3)
        plt.xlabel('Longitude')
        plt.ylabel('Latitude')
        plt.title('Number of cross-correlations per station')

    def plot_connections_of_station(self, station_code):
        '''

        Parameters
        ----------
        cd : TYPE
            DESCRIPTION.

        Returns
        -------
        None.

        '''
        fig, ax = plt.subplots(1, 1, dpi=300)
        no_station_connections = []

        station = self.get_station(station_code)
        station_code = station.code
        station_count = 0
        for correlation in self.correlations:
            if station.included_in_inversion == False:
                continue
            # If the station is not in the correlation then continue to next
            # correlation.
            if correlation.station1_code != station_code:
                if correlation.station2_code != station_code:
                    continue
            if isinstance(correlation.t_app[-1], str):
                continue
            if np.isnan(correlation.t_app[-1]):
                continue
            station_count += 1
            sta1_temp = self.get_station(correlation.station1_code)
            sta2_temp = self.get_station(correlation.station2_code)
            ax.plot([sta1_temp.longitude, sta2_temp.longitude],
                    [sta1_temp.latitude, sta2_temp.latitude],
                    color='k', alpha=0.1, linewidth=0.4)
        no_station_connections.append(station_count)
        ax.annotate(station_code + "\n" + str(station_count),
                    [station.longitude, station.latitude],
                    zorder=9999, alpha=0.8,
                    path_effects=[pe.withStroke(linewidth=4,
                                                foreground="white")])
        lats = [sta.latitude for sta in self.stations]
        lons = [sta.longitude for sta in self.stations]
        colors = ['C0' if station.needs_correction else 'C1' for station in
                  self.stations]
        ax.scatter(lons, lats, c=colors, zorder=999,
                   edgecolor='k',
                   alpha=0.7)
        ax.scatter([], [], label='OBS station', color='C0', edgecolor='k')
        ax.scatter([], [], label='Land station', color='C1', edgecolor='k')
        plt.legend(loc=3)
        plt.xlabel('Longitude')
        plt.ylabel('Latitude')
        plt.title('Cross-correlations of station: ' + station_code)

    def remove_outiers(self, max_error=2, iteration=-1):
        for sta1 in self.stations:
            average_residuals = []
            for sta2 in self.stations:
                if sta1 == sta2:
                    average_residuals.append(np.nan)
                    continue
                station1_code, station2_code = sta1.code, sta2.code
                correlations = self.get_correlations_of_stationpair(station1_code,
                                                                    station2_code)
                if len(correlations) == 0:
                    average_residuals.append(np.nan)
                    continue
                station1 = self.get_station(correlations[0].station1_code)
                station2 = self.get_station(correlations[0].station2_code)

                if station1.included_in_inversion == False:
                    continue
                if station2.included_in_inversion == False:
                    continue
                a_val_sta1, a_val_sta2 = 0, 0
                b_val_sta1, b_val_sta2 = 0, 0
                if station1.needs_correction:
                    a_val_sta1 = float(station1.a[iteration])
                    b_val_sta1 = float(station1.b[iteration])

                if station2.needs_correction:
                    a_val_sta2 = float(station2.a[iteration])
                    b_val_sta2 = float(station2.b[iteration])

                for correlation in correlations:
                    t_N_lps = correlation.t_N_lps
                    dt_ins_i = a_val_sta1 * t_N_lps + b_val_sta1
                    dt_ins_j = a_val_sta2 * t_N_lps + b_val_sta2
                    predicted = 2 * (dt_ins_i - dt_ins_j)
                    observed = (float(correlation.t_app[iteration]))
                    residual = (observed - predicted)
                    if abs(residual) > abs(max_error):
                        correlation.t_app[iteration] = np.nan

    def plot_obs_and_pred_shifts_with_land_stations(self, station1_code,
                                                    iteration=-1):
        '''
        Parameters
        ----------
        station1_code : TYPE
            DESCRIPTION.

        Raises
        ------
        Exception
            DESCRIPTION.

        Returns
        -------
        None.

        '''
        if self.get_station(station1_code).needs_correction == False:
            raise Exception("Station doesn't need correction")

        plt.figure(dpi=300)
        for station in self.stations:
            if station1_code == station.code:
                continue
            if station.needs_correction:
                continue
            station2_code = station.code

            correlations = self.get_correlations_of_stationpair(station1_code,
                                                                station2_code)
            if len(correlations) == 0:
                continue
            station1 = self.get_station(correlations[0].station1_code)
            station2 = self.get_station(correlations[0].station2_code)

            # TODO: Check that this fix works.
            if station1.included_in_inversion == False:
                continue
            if station2.included_in_inversion == False:
                continue
            a_val_sta1 = 0
            a_val_sta2 = 0
            b_val_sta1 = 0
            b_val_sta2 = 0
            if station1.needs_correction:
                a_val_sta1 = float(station1.a[iteration])
                b_val_sta1 = float(station1.b[iteration])

            if station2.needs_correction:
                a_val_sta2 = float(station2.a[iteration])
                b_val_sta2 = float(station2.b[iteration])

            estimates = []
            firstday_finalday = [0, 300]
            for day in firstday_finalday:  # First day and final day to plot predicted values
                dt_ins_station1 = (a_val_sta1 * day + b_val_sta1)
                dt_ins_station2 = a_val_sta2 * day + b_val_sta2
                estimate = 2 * (dt_ins_station1 - dt_ins_station2)
                estimates.append(estimate)

            dates = [corr.t_N_lps for corr in correlations]
            t_apps = []
            for correlation in correlations:
                t_app = float(correlation.t_app[iteration])
                if correlation.station1_code == station1_code:
                    t_apps.append(-t_app)
                else:
                    t_apps.append(t_app)
            plt.scatter(dates, t_apps, facecolors='none', edgecolor='k',
                        zorder=99,
                        alpha=0.6)

        plt.plot(
            firstday_finalday,
            estimates,
            color="C3",
            alpha=0.9,
            zorder=-1)
        plt.title("Shifts of correlations between" + station1_code +
                  " and land stations")
        plt.xlabel("Number of days after reference date")
        plt.ylabel("Time shift [s]")
        plt.plot([], [], color="C3", label="Predicted (" +
                 r"$2\delta t^{ins}_i - 2\delta t^{ins}_j$)")
        plt.scatter([], [], facecolors='none', edgecolor='k',
                    label="Observed (" +
                    r"$t^{+, app}_{i, j} + t^{-, app}_{i, j}$)")
        plt.annotate(str(self.reference_time)[:10] + "\n" + str(estimates[0])[:5],
                     (firstday_finalday[0], estimates[0]))
        plt.annotate(str(self.reference_time +
                         firstday_finalday[1] * 86400)[:10] + "\n" +
                     str(estimates[1])[:5],
                     (firstday_finalday[1], estimates[1]))
        plt.scatter([firstday_finalday[0], firstday_finalday[1]],
                    [estimates[0], estimates[1]], facecolors='none',
                    edgecolor='C3')
        plt.legend()

    def plot_obs_and_pred_shifts_of_stationpair(self, station1_code,
                                                station2_code,
                                                iteration=-1):
        correlations = self.get_correlations_of_stationpair(station1_code,
                                                            station2_code)
        if len(correlations) == 0:
            raise Exception("No cross-correlations between these two stations")
        station1 = self.get_station(correlations[0].station1_code)
        station2 = self.get_station(correlations[0].station2_code)

        # TODO: Check that this fix works.
        if station1.included_in_inversion == False:
            return ("Station: " + station1.code + " not included in inversion")
        if station2.included_in_inversion == False:
            return ("Station: " + station2.code + " not included in inversion")

        a_val_sta1 = 0
        a_val_sta2 = 0
        b_val_sta1 = 0
        b_val_sta2 = 0
        if station1.needs_correction:
            a_val_sta1 = float(station1.a[iteration])
            b_val_sta1 = float(station1.b[iteration])

        if station2.needs_correction:
            a_val_sta2 = float(station2.a[iteration])
            b_val_sta2 = float(station2.b[iteration])

        t_apps = []
        predicted = []
        dates = []
        for correlation in correlations:
            t_N_lps = correlation.t_N_lps
            dt_ins_station1 = (a_val_sta1 * t_N_lps + b_val_sta1)
            dt_ins_station2 = a_val_sta2 * t_N_lps + b_val_sta2
            estimate = 2 * (dt_ins_station1 - dt_ins_station2)
            predicted.append(estimate)
            t_app = float(correlation.t_app[iteration])
            t_apps.append((t_app))
            dates.append(t_N_lps)
        if all(np.isnan(i) for i in t_apps):
            raise Exception("All the apparent times are np.nan")
        plt.figure(dpi=300)
        plt.scatter(dates, t_apps, facecolors='none', edgecolor='k', zorder=99,
                    alpha=0.6,
                    label="Observed ($t^{+, app}_{i, j} + t^{-, app}_{i, j}$)")
        plt.plot(dates, predicted, color='C3', zorder=99,
                 alpha=0.6,
                 label="Predicted ($2\\delta t^{ins}_i - 2\\delta t^{ins}_j$)")
        plt.title(station1.code + "-" + station2.code)
        plt.ylabel("Time shift [s]")
        plt.xlabel("Days from reference date")
        plt.legend()
        plt.show()

    def plot_observed_vs_predicted(self, station2plot="all",
                                   iteration=-1):
        '''
        Function to plot the observed shift (t_app) vs the predicted shift
        (2dt_ins_i - 2dt_ins_j)

        Parameters
        ----------
        station2plot : can be "all" or the name of the station to plot.
            DESCRIPTION.
        iteration : TYPE, optional
            DESCRIPTION. The default is -1.

        Returns
        -------
        None.

        '''
        all_observed = []
        all_predicted = []
        for i, sta1 in enumerate(self.stations):
            # If the station has no solution yet, dont include it.
            if len(sta1.a) == 0:
                continue
            # If station2plot is different to 'all' then stop the for loop for only that
            # station
            if station2plot != 'all':
                if sta1.code != station2plot:
                    continue
                i = 0

            station1_code = sta1.code
            for sta2 in self.stations[i:]:
                if station1_code == sta2.code:
                    continue

                # If the station has no solution yet, dont include the
                # correlation.
                if sta2.needs_correction:
                    if len(sta2.a) == 0:
                        continue

                station2_code = sta2.code
                correlations = self.get_correlations_of_stationpair(station1_code,
                                                                    station2_code)
                if len(correlations) == 0:
                    continue
                station1 = self.get_station(correlations[0].station1_code)
                station2 = self.get_station(correlations[0].station2_code)

                # TODO: Check that this fix works.
                if station1.included_in_inversion == False:
                    continue
                if station2.included_in_inversion == False:
                    continue

                a_val_sta1, a_val_sta2 = 0, 0
                b_val_sta1, b_val_sta2 = 0, 0
                if station1.needs_correction:
                    a_val_sta1 = float(station1.a[iteration])
                    b_val_sta1 = float(station1.b[iteration])
                if station2.needs_correction:
                    a_val_sta2 = float(station2.a[iteration])
                    b_val_sta2 = float(station2.b[iteration])

                for correlation in correlations:
                    t_N_lps = correlation.t_N_lps
                    dt_ins_i = a_val_sta1 * t_N_lps + b_val_sta1
                    dt_ins_j = a_val_sta2 * t_N_lps + b_val_sta2
                    predicted = 2 * (dt_ins_i - dt_ins_j)
                    observed = (float(correlation.t_app[iteration]))

                    if np.isnan(observed) == False:
                        all_observed.append(observed)
                        all_predicted.append(predicted)
        plt.figure(dpi=300)
        plt.scatter(all_predicted, all_observed, facecolors='none', edgecolor='k', zorder=99,
                    alpha=0.6)

        plt.plot((-50, 100), (-50, 100), ls='--', color="C3", label="1:1 line")
        plt.title(station2plot)
        plt.ylabel("Observed ($t^{+, app}_{i, j} + t^{-, app}_{i, j}$)")
        plt.xlabel("Predicted ($2\\delta t^{ins}_i - 2\\delta t^{ins}_j$)")
        plt.legend()
        plt.xlim(min(all_predicted) - 0.25, max(all_predicted) + 0.25)
        plt.ylim(min(all_observed) - 0.25, max(all_observed) + 0.25)
        plt.show()

    def plot_matrix_diff_observed_predicted(self, iteration=-1):

        column_headers = [sta.code for sta in self.stations]
        row_headers = [sta.code for sta in self.stations]
        matrix = []
        for sta1 in self.stations:
            average_residuals = []
            for sta2 in self.stations:
                if sta1 == sta2:
                    average_residuals.append(np.nan)
                    continue
                station1_code, station2_code = sta1.code, sta2.code
                correlations = self.get_correlations_of_stationpair(station1_code,
                                                                    station2_code)
                if len(correlations) == 0:
                    average_residuals.append(np.nan)
                    continue
                station1 = self.get_station(correlations[0].station1_code)
                station2 = self.get_station(correlations[0].station2_code)

                # TODO: Check that this fix works.
                if station1.included_in_inversion == False:
                    continue
                if station2.included_in_inversion == False:
                    continue
                a_val_sta1, a_val_sta2 = 0, 0
                b_val_sta1, b_val_sta2 = 0, 0
                if station1.needs_correction:
                    a_val_sta1 = float(station1.a[iteration])
                    b_val_sta1 = float(station1.b[iteration])

                if station2.needs_correction:
                    a_val_sta2 = float(station2.a[iteration])
                    b_val_sta2 = float(station2.b[iteration])

                residuals = []
                for correlation in correlations:
                    t_N_lps = correlation.t_N_lps
                    dt_ins_i = a_val_sta1 * t_N_lps + b_val_sta1
                    dt_ins_j = a_val_sta2 * t_N_lps + b_val_sta2
                    predicted = 2 * (dt_ins_i - dt_ins_j)
                    observed = (float(correlation.t_app[iteration]))
                    residual = (observed - predicted)
                    if np.isnan(observed) == False:
                        residuals.append(residual)
                average_residuals.append((np.mean(residuals)))
            matrix.append(average_residuals)

        df = pd.DataFrame(columns=column_headers,
                          index=row_headers,
                          data=np.array(matrix).T)

        sns.set(font_scale=0.6)
        plt.figure(dpi=300)
        g = sns.heatmap(df, cmap=sns.diverging_palette(240, 10, n=9, as_cmap=True),
                        xticklabels=True, yticklabels=True,
                        linewidths=0.1,
                        linecolor='k',
                        cbar_kws={'label': '|$t^{+, app}_{i, j} + t^{-, app}_{i, j}$' +
                                  " - ($2\\delta t^{ins}_i - 2\\delta t^{ins}_j$)|"},
                        # vmin=-2.0,vmax=2.0,
                        center=0.00)
        cbar = g.collections[0].colorbar
        cbar.ax.tick_params(labelsize=10)
        g.figure.axes[-1].yaxis.label.set_size(12)
        # g.set_facecolor('white')
        g.set_facecolor('dimgrey')
        plt.xlabel("Station code", fontsize=12)
        plt.ylabel("Station code", fontsize=12)
        plt.show()
        matplotlib.rc_file_defaults()

    def plot_correlation(self, filepath, min_t=-40, max_t=40, freqmin=0.15,
                         freqmax=0.3, iteration=-1):
        '''
        Function to plot the correlation. It shows the correction using the
        observed drift and the correction using the predicted drift.

        Parameters
        ----------
        filepath : TYPE
            DESCRIPTION.

        Returns
        -------
        None.

        '''
        corr = self.get_correlation_of_file(filepath)
        station1 = self.get_station(corr.station1_code)
        station2 = self.get_station(corr.station2_code)
        if station1.needs_correction:
            a_val_sta1, b_val_sta1 = station1.a[iteration], station1.b[iteration]
        else:
            a_val_sta1, b_val_sta1 = 0, 0
        if station2.needs_correction:
            a_val_sta2, b_val_sta2 = station2.a[iteration], station2.b[iteration]
        else:
            a_val_sta2, b_val_sta2 = 0, 0
        t_N_lps = corr.t_N_lps
        dt_ins_i = a_val_sta1 * t_N_lps + b_val_sta1
        dt_ins_j = a_val_sta2 * t_N_lps + b_val_sta2
        predicted_shift = 2 * (dt_ins_i - dt_ins_j)

        observed_shift = corr.t_app[iteration]
        ac_i0, ac_if = int(
            corr.acausal_from_index), int(
            corr.acausal_until_index)
        c_i0, cif = int(corr.causal_from_index), int(corr.causal_until_index)

        tr = read_correlation_file(corr.file_path)
        tr = tr.filter('bandpass', freqmin=freqmin, freqmax=freqmax,
                       corners=4, zerophase=True)
        start = -tr.times()[-1] / 2.
        end = tr.times()[-1] / 2.

        # tr=tr.normalize()
        t = np.linspace(start, end, tr.stats.npts)
        data = tr.data

        f, ax0 = plt.subplots(1, 1, sharey=True, dpi=300)
        f, ax1 = plt.subplots(1, 1, sharey=True, dpi=300)
        f, ax2 = plt.subplots(1, 1, sharey=True, dpi=300)
        f, ax3 = plt.subplots(1, 1, sharey=True, dpi=300)
        ax0.plot(t, data, label=str(tr.stats.average_date)[:10])
        ax0.plot(t[ac_i0:ac_if], data[ac_i0:ac_if], color='C3')
        ax0.plot(t[c_i0:cif],
                 data[c_i0:cif],
                 label="Estimated causal and acausal wave",
                 color='C3')

        ax1.plot(t, data, label=tr.stats.average_date)
        ax1.plot(t, data[::-1], ls='--', label=tr.stats.average_date)

        ax2.plot(t - observed_shift / 2, data, label=tr.stats.average_date)
        ax2.plot(t + observed_shift / 2, data[::-1], ls='--',
                 label="Mirrored signal")

        ax3.plot(t - predicted_shift / 2, data)
        ax3.plot(t + predicted_shift / 2, data[::-1], ls='--',
                 label="Mirrored signal")

        ax3.set_xlabel('Time [s]')
        [axs.set_xlim(min_t, max_t) for axs in [ax0, ax1, ax2, ax3]]
        [axs.set_ylabel('Amplitudes') for axs in [ax0, ax1, ax2, ax3]]
        ax1.set_title('Before correction ' + tr.stats.station_pair)
        ax2.set_title('After correction using observed shift ($t^{+, app}_{i, j}' +
                      ' + t^{-, app}_{i, j}$)')
        ax3.set_title('After correction using estimated shift ($2\\delta t^{ins}_i - ' +
                      '2\\delta t^{ins}_j$)')
        plt.tight_layout()
        ax0.legend(loc='best')
        plt.show()

    def plot_errorbars(self, iteration=-1):
        land_stations, land_errors = [], []
        obs_stations, obs_errors = [], []
        for i, sta1 in enumerate(self.stations):
            station1_code = sta1.code
            for sta2 in self.stations:
                if station1_code == sta2.code:
                    continue

                station2_code = sta2.code
                correlations = self.get_correlations_of_stationpair(station1_code,
                                                                    station2_code)
                if len(correlations) == 0:
                    continue
                station1 = self.get_station(correlations[0].station1_code)
                station2 = self.get_station(correlations[0].station2_code)

                # TODO: Check that this fix works.
                if station1.included_in_inversion == False:
                    continue
                if station2.included_in_inversion == False:
                    continue
                a_val_sta1, a_val_sta2 = 0, 0
                b_val_sta1, b_val_sta2 = 0, 0
                if station1.needs_correction:
                    a_val_sta1 = float(station1.a[iteration])
                    b_val_sta1 = float(station1.b[iteration])

                if station2.needs_correction:
                    a_val_sta2 = float(station2.a[iteration])
                    b_val_sta2 = float(station2.b[iteration])

                for correlation in correlations:
                    t_N_lps = correlation.t_N_lps
                    dt_ins_i = a_val_sta1 * t_N_lps + b_val_sta1
                    dt_ins_j = a_val_sta2 * t_N_lps + b_val_sta2
                    predicted = 2 * (dt_ins_i - dt_ins_j)
                    observed = (float(correlation.t_app[iteration]))

                    if np.isnan(observed) == False:
                        error = (observed - predicted)
                        if sta1.needs_correction:
                            obs_stations.append(sta1.code)
                            obs_errors.append(error)
                        else:
                            land_stations.append(sta1.code)
                            land_errors.append(error)
                        # row_names.append(station1_code)

        obs_df = pd.DataFrame(index=obs_stations,
                              data=obs_errors,
                              columns=["Error"])
        land_df = pd.DataFrame(index=land_stations,
                               data=land_errors,
                               columns=["Error"])

        plt.figure(dpi=300)
        fig = sns.boxplot(y='Error', x=obs_df.index, data=obs_df)
        sns.swarmplot(x=obs_df.index, y="Error", data=obs_df, color=".25",
                      size=1.5)
        fig.set_xlabel('Stations')
        fig.set_title('|$t^{+, app}_{i, j} + t^{-, app}_{i, j}$' +
                      " - ($2\\delta t^{ins}_i - 2\\delta t^{ins}_j$)|")
        fig.set_xticklabels(fig.get_xmajorticklabels(), fontsize=8)

        plt.figure(dpi=300)
        fig = sns.boxplot(y='Error', x=land_df.index, data=land_df)
        sns.swarmplot(x=land_df.index, y="Error", data=land_df, color=".25",
                      size=1.5)
        fig.set_xlabel('Stations')
        fig.set_title('|$t^{+, app}_{i, j} + t^{-, app}_{i, j}$' +
                      " - ($2\\delta t^{ins}_i - 2\\delta t^{ins}_j$)|")
        fig.set_xticklabels(fig.get_xmajorticklabels(), fontsize=5)

#    def plot_inv_correlations(self):
#        '''
#        Make the plot including the the map of Iceland.
#        This method is adapted to plot the dataset of Iceland. If you are
#        using other dataset then use plot_inventory_correlations()
#        Returns
#        -------
#        None.
#
#        '''
#        llcrnrlon = -24.15
#        urcrnrlon = -21.75
#        llcrnrlat = 63.4
#        urcrnrlat = 64.2
#        n_ticks = 5  # Number of ticks in y and x-axes.
#        fig, ax = plt.subplots(1, 1, dpi=300)
#        map = Basemap(llcrnrlon=llcrnrlon,
#                      llcrnrlat=llcrnrlat,
#                      urcrnrlon=urcrnrlon,
#                      urcrnrlat=urcrnrlat,
#                      epsg=3857, resolution='h')
#
#        no_station_connections = []
#        for station in self.stations:
#            station_code = station.code
#            station_count = 0
#
#            # TODO: Check that this fix works.
#            if station.included_in_inversion == False:
#                continue
#
#            for correlation in self.correlations:
#                # If it is the first iteration then count all the available
#                # cross-correlations.
#                if self.iteration != 0:
#                    # If it is no longer the 0 iteration then show only the
#                    # cross-correlations that were included in the inversion.
#                    # If the station is not in the correlation then continue to next
#                    # correlation.
#                    if station.included_in_inversion == False:
#                        continue
#                    if isinstance(correlation.t_app[-1], str):
#                        continue
#                    if np.isnan(correlation.t_app[-1]):
#                        continue
#
#                if correlation.station1_code != station_code:
#                    if correlation.station2_code != station_code:
#                        continue
#                station_count += 1
#                sta1_temp = self.get_station(correlation.station1_code)
#                sta2_temp = self.get_station(correlation.station2_code)
#                x1, y1 = map(sta1_temp.longitude, sta1_temp.latitude)
#                x2, y2 = map(sta2_temp.longitude, sta2_temp.latitude)
#
#                map.plot([x1, x2],
#                         [y1, y2],
#                         color='k', alpha=0.1, linewidth=0.4)
#            no_station_connections.append(station_count)
#            if station.needs_correction:
#                x1, y1 = map(station.longitude, station.latitude)
#                ax.annotate(station_code + "\n" + str(station_count),
#                            [x1, y1],
#                            zorder=9999, alpha=0.8,
#                            path_effects=[pe.withStroke(linewidth=4,
#                                                        foreground="white")])
#        lats = [sta.latitude for sta in self.stations]
#        lons = [sta.longitude for sta in self.stations]
#        colors = ['C0' if station.needs_correction else 'C1' for station in
#                  self.stations]
#        x, y = map(lons, lats)
#        map.scatter(x, y, c=colors, zorder=999,
#                    edgecolor='k', s=no_station_connections,
#                    alpha=0.7)
#        map.scatter([], [], label='OBS station', color='C0', edgecolor='k')
#        map.scatter([], [], label='Land station', color='C1', edgecolor='k')
#        plt.legend(loc=3)
#        plt.xlabel('Longitude')
#        plt.ylabel('Latitude')
#        plt.title('Number of cross-correlations per station')
#
#        x, y = map(np.linspace(llcrnrlon, urcrnrlon, n_ticks),
#                   np.linspace(llcrnrlat, urcrnrlat, n_ticks)
#                   )
#        plt.xticks(x, np.linspace(llcrnrlon, urcrnrlon, n_ticks))
#        plt.yticks(y, np.linspace(llcrnrlat, urcrnrlat, n_ticks))
#        map.drawmapscale(urcrnrlon - 0.4, llcrnrlat + 0.1, llcrnrlon + 1, llcrnrlat, 50,
#                         barstyle='fancy', units='km', labelstyle='simple',
#                         fillcolor1='w', fillcolor2='black', fontcolor='black', fontsize=10)
#
#        map.drawmapboundary(fill_color='skyblue')
#        # Fill the continents with the land color
#        map.fillcontinents(color='lightgray')
#        map.drawcoastlines(color='grey')

# if __name__ == "__main__":
#     main()
# %%
# from main import Processing_parameters, Clock_drift
# import time
# ext = '/Users/localadmin/Dropbox/GitHub/'
# # Parameters
# station_file = ext + "station_info"
# datadir = ext + "data_20mins"

# days_apart = 50
# min_no_corr_periods = 3
# min_no_correlations4obs = 8
# min_no_correlations4land = 5


# params = Processing_parameters(
#                  freqmin = 0.15, # Low freq. for the bandpass filter
#                  freqmax = 0.3, # High freq. for the bandpass filter
#                  ref_vel = 2500, # m/s
#                  dist_trh = 2.0, # Minimum station separation in terms of wavelength
#                  snr_trh = 30, # Signal-to-noise ratio threshold
#                  noise_st = 240, # start of the noise window.
#                  dt_err = 0.004, # Sampling interval needs to be multiple of this value.
#                  resp_details = False)

# cd = Clock_drift(station_file, datadir, reference_time = '2014-08-21T00:00:00.000000Z',
#                  list_of_processing_parameters=[params])
# cd.build_matrices()


# #%%
# skew_values_file = "/Users/localadmin/Dropbox/GitHub/clock_errors/clock_errors_py/jupyter-tutorials/skew_values.csv"
# skew_df = pd.read_csv(skew_values_file, delimiter=",", header=0)

# O20_clock_drift_s = 0
# O14_clock_drift_s = 0
# for station, starttime, endtime, skew in zip(skew_df["Sensor code"],
#                                              skew_df["Start time"],
#                                              skew_df["End time"],
#                                              skew_df["Skew microseconds"]):
#     endtime = str(endtime)
#     if endtime == "nan":
#         continue

#     skew_s = float(skew)/(10**6) #converted to seconds
#     starttime = obspy.UTCDateTime(starttime)
#     endtime = obspy.UTCDateTime(endtime)

#     clock_drift_s = skew_s/(endtime-starttime)
#     if station == "O20":
#         O20_clock_drift_s = clock_drift_s
#     elif station == "O14":
#         O14_clock_drift_s = clock_drift_s
# #%%
# correlations = cd.get_correlations_of_stationpair("O20", "O14")
# t_apps = [c.t_app[0]/2 for c in correlations]
# aprioris = [-c.apriori_dt1  - c.apriori_dt2 for c in correlations]
# days = [(c.average_date - cd.reference_time)/86400 for c in correlations]
# plt.scatter(days, t_apps, label=r"$t^{+, app}_{i, j} + t^{-, app}_{i, j}$)")
# plt.scatter(days, aprioris, label="Apriori")
# plt.scatter(days,
#             (np.array(days)*O20_clock_drift_s + np.array(days)*O14_clock_drift_s)*86400,
#             label="Skew")
# plt.legend()
# plt.show()
