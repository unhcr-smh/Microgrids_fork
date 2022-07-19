#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Pierre Haessig, Evelise Antunes — 2022
""" Microgrid operation modeling
"""

from dataclasses import dataclass
import numpy as np
from components import Microgrid


@dataclass
class OperationStats:
    """Aggregated statistics over the simulated Microgrid operation

    (simulation duration is assumed to be 1 year)
    """
    # Load statistics
    served_energy: float = 0.0
    "energy served to the load (kWh/y)"
    shed_energy: float = 0.0
    "shed energy, that is not served to the load (kWh/y)"
    shed_max: float = 0.0
    "maximum load shedding power (kW)"
    shed_hours: float = 0.0
    "cumulated duration of load shedding (h/y)"
    shed_duration_max: float = 0.0
    "maximum consecutive duration of load shedding (h)"
    shed_rate: float = 0.0
    "ratio of shed energy to the cumulated desired load (∈ [0,1])"

    # Dispatchable generator statistics
    gen_hours: float = 0.0
    "cumulated operating hours of the dispatchable generator (h/y)"
    gen_fuel: float = 0.0
    "Diesel consumption in one year (L)"

    # Energy storage (e.g. battery) statistics
    storage_cycles: float = 0.0
    "cumulated cycling of the energy storage (cycles/y)"
    storage_throughput: float = 0.0
    "cumulated energy throughput (in and out) of the energy storage (kWh/y)"

    # Non-dispatchable (typ. renewables) sources statistics
    spilled_energy: float = 0.0
    "spilled energy (typ. from excess of renewables) (kWh/y)"
    spilled_max: float = 0.0
    "maximum spilled power (typ. from excess of renewables) (kW)"
    spilled_rate: float = 0.0
    "ratio of spilled energy to the energy potentially supplied by renewables (∈ [0,1])"
    renew_potential_energy: float = 0.0
    "energy potentially supplied by renewables in the absence spillage (kWh/y)"
    renew_energy: float = 0.0
    "energy actually supplied by renewables when substracting spillage (kWh/y)"
    renew_rate: float = 0.0
    "ratio of energy actually supplied by renewables to the energy served to the load (∈ [0,1])"


def dispatch(Pnl_req, Psto_cmax, Psto_dmax, Pgen_max) -> tuple[float, float, float, float]:
    """Energy dispatch decision for a "load-following-style" strategy.

    This simple rule-based energy dispatch assumes the Microgrid has
    one energy storage and one dispatchable generator.

    The load is implicitely fed for non-dispatchable sources first, so that
    the dispatch decision is only concerned by the net load request `Pnl_req`
    (desired load - non dispatchable potential).

    This net load is prioritarily fed by the energy storage and (when empty)
    by the dispatchable generator.

    Storage power uses a *generator convention*: positive when discharging
    and negative when charging.

    Parameters
    ----------
    Pnl_req: float
        Requested net load (desired load - non dispatchable potential) (kW).
    Psto_cmax: float
        Maximum storage charge power (kW, < 0).
    Psto_dmax:
        Maximum storage discharge power (kW).
    Pgen_max: float
        Rated power of the dispatchable generator (kW).

    Returns
    -------
    Pgen: float
        Power supplied by the dispatchable generator (kW).
    Psto: float
        Power supplied by the energy storage (kW).
    Pspill: float
        Spilled power, typically realized by curtailing renewables (kW).
    Pshed: float
        Shed power from the load (kW).
    """
    Pspill = 0.0
    Pshed = 0.0
    # Pnl_req >= 0 - load excess - after evaluating the production (Pnl = Pload - VRE generation)
    if Pnl_req >= 0.0:
        # storage discharging --> Psto positive
        if Pnl_req >= Psto_dmax:    # max(storage)
            Psto = Psto_dmax      # max(storage)
            if Pnl_req - Psto >= Pgen_max:  # max(generator)
                Pgen  = Pgen_max
                Pshed = Pnl_req - Psto - Pgen
            else:
                Pgen = Pnl_req - Psto
        else:
            Pgen  = 0.0
            Psto = Pnl_req

    # Pnl_req < 0 - VRE excess
    else: # Pnl_req < 0.0
        Pgen  = 0.0
        # storage charging --> Psto negative
        if Pnl_req >= Psto_cmax:    # min(storage)
            Psto = Pnl_req
        else:
            Psto = Psto_cmax      # min(storage)
            Pspill  = Psto - Pnl_req
    # end if Pnl_req >= 0.0

    return Pgen, Psto, Pspill, Pshed


def operation(mg:Microgrid, recorder=None) -> OperationStats:
    """Simulate the operation of Microgrid project `mg`.

    Time series are recorded if `recorder` is a `Recorder`.

    Returns operational statistics.
    """
    # Remark: assuming all non-dispatchable sources are renewable!
    renew_productions = [nd.production() for nd in mg.nondispatchables]
    renew_potential = sum(renew_productions)

    # Desired net load
    Pnl_request = mg.load - renew_potential

    # Fixed parameters
    K = len(mg.load)
    dt = mg.project.timestep
    Psto_pmax =  mg.storage.discharge_rate_max * mg.storage.energy_rated
    Psto_pmin = -mg.storage.charge_rate_max * mg.storage.energy_rated

    # Initialization of loop variables
    # Initial storage state
    Esto = mg.storage.SoC_ini * mg.storage.energy_rated
    # Operation statistics counters initialiazed at zero
    op_st = OperationStats()
    shed_duration = 0.0 # duration of current load shedding event (h)

    if recorder:
        recorder.init(Pgen=K, Psto=K, Esto=K+1, Pspill=K, Pshed=K)


    ### Operation simulation loop
    for k in range(K):

        ### Decide energy dispatch
        Psto_emin = - (mg.storage.energy_max - Esto) / ((1 - mg.storage.loss) * dt)
        Psto_emax = (Esto - mg.storage.energy_min) / ((1 + mg.storage.loss) * dt)
        Psto_dmax = min(Psto_emax, Psto_pmax)
        Psto_cmax = max(Psto_emin, Psto_pmin)

        Pgen, Psto, Pspill, Pshed = dispatch(
            Pnl_request[k],
            Psto_cmax, Psto_dmax,
            mg.dispatchable.power_rated)

        if recorder:
            recorder.rec(Pgen=Pgen, Psto=Psto, Esto=Esto, Pspill=Pspill, Pshed=Pshed)

        ### Aggregate operation statistics

        # Load statistics
        op_st.shed_energy += Pshed*dt
        op_st.shed_max = max(op_st.shed_max, Pshed)
        if Pshed > 0.0:
            op_st.shed_hours += dt
            shed_duration += dt
            op_st.shed_duration_max = max(op_st.shed_duration_max, shed_duration)
        else:
            # reset duration of current load shedding event
            shed_duration = 0.0

        # Dispatchable generator statistics
        if Pgen > 0.0: # Generator ON
            op_st.gen_hours += dt
            fuel_rate = mg.dispatchable.fuel_intercept * mg.dieselgenerator.power_rated +\
                          mg.dispatchable.fuel_slope * Pgen # (l/h)
            op_st.gen_fuel += fuel_rate*dt

        # Energy storage (e.g. battery) statistics
        op_st.storage_throughput += abs(Psto)*dt

        # Non-dispatchable (typ. renewables) sources statistics
        op_st.spilled_energy += Pspill*dt
        op_st.spilled_max = max(op_st.spilled_max, Pspill)
    # end for each instant k

    if recorder:
        recorder.rec(Esto=Esto)# Esto at last instant

    # Some more aggregated operation statistics
    load_energy = np.sum(mg.load)*dt
    op_st.served_energy = load_energy - op_st.shed_energy
    op_st.shed_rate = op_st.shed_energy / load_energy
    op_st.storage_cycles = op_st.storage_throughput / (2*mg.storage.energy_max)
    op_st.spilled_rate = op_st.spilled_energy / op_st.renew_potential_energy
    op_st.renew_potential_energy = np.sum(renew_potential)
    op_st.renew_energy = op_st.renew_potential_energy - op_st.spilled_energy
    op_st.renew_rate = op_st.renew_energy/op_st.served_energy

    return op_st