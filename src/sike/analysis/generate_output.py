import xarray as xr
import pandas as pd
import numpy as np

from sike.core import SIKERun


def generate_output(case: SIKERun) -> xr.Dataset:
    """Generate an xarray dataset containing states and densities

    :param case: A SIKERun case
    :return: An xarray dataset containing state information and densities
    """
    # Extract relevant information (densities & states) from the SIKERun object
    dens = case.impurity.dens
    x = case.xgrid * case.x_norm
    k = [s.id for s in case.impurity.states]
    v = case.vgrid * case.v_th
    selected_state_cols = [
        "id",
        "Z",
        "n",
        "l",
        "j",
        "stat_weight",
        "iz_energy",
        "energy_from_gs",
    ]
    states = [
        {"state_" + k: v for k, v in s.__dict__.items() if k in selected_state_cols}
        for s in case.impurity.states
    ]
    selected_trans_cols = ["from_id", "to_id", "type", "rate", "rate_inv"]
    transitions = [
        {
            "transition_" + k: v
            for k, v in s.__dict__.items()
            if k in selected_trans_cols
        }
        for s in case.impurity.transitions
    ]
    transitions_df = pd.DataFrame(transitions)
    transitions_df.index.name = "i"
    transitions_df.rename(
        columns={
            "transition_from_id": "transition_from_k",
            "transition_to_id": "transition_to_k",
        }
    )

    # Generate Dataarrays
    dens_da = xr.DataArray(dens * case.n_norm, coords={"x": x, "k": k})
    Te_da = xr.DataArray(case.Te * case.T_norm, coords={"x": x})
    ne_da = xr.DataArray(case.ne * case.n_norm, coords={"x": x})
    fe_da = xr.DataArray(
        case.fe * case.n_norm / (case.v_th**3), coords={"v": v, "x": x}
    )
    rate_mats_da = xr.DataArray(
        [mat * case.t_norm for mat in case.rate_mats],
        coords={"x": x, "j": k, "k": k},
    )  # TODO: Check normalisation here

    # Generate the states dataset from a pandas dataframe
    states_df = pd.DataFrame(states).rename(columns={"state_id": "k"}).set_index("k")
    output_ds = xr.Dataset.from_dataframe(states_df)

    # Generate transitions dataset and merge with the states dataset
    transitions_ds = xr.Dataset.from_dataframe(transitions_df)
    output_ds = output_ds.merge(transitions_ds)

    # Combine with other dataarrays
    output_ds["nk"] = dens_da
    output_ds["Te"] = Te_da
    output_ds["ne"] = ne_da
    output_ds["fe"] = fe_da
    output_ds["M"] = rate_mats_da

    # Add metadata and other info
    output_ds.attrs["metadata"] = get_metadata(case)
    output_ds = add_coordinate_info(output_ds)
    output_ds = add_data_info(output_ds)

    return output_ds


def get_metadata(case: SIKERun) -> dict:
    """Generate a dictionary of metadata from the SIKERun case

    :param case: SIKERun case containing metadata attributes
    :return: Dictionary of metadata
    """
    # Add metadata
    metadata_dict = {
        "element": case.impurity.longname,
        "resolve_l_states": case.resolve_l,
        "resolve_j_states": case.resolve_j,
        "ionization": case.ionization,
        "radiative_recombination": case.radiative_recombination,
        "excitation": case.excitation,
        "emission": case.emission,
        "autoionization": case.autoionization,
        "atom_data_savedir": case.atom_data_savedir,
    }
    metadata_dict

    return metadata_dict


def add_data_info(ds: xr.Dataset) -> xr.Dataset:
    """Add information on the dataarrays to the dataset

    :param ds: Xarray dataset built from SIKERun
    :return: Modified xarray dataset
    """
    # Densities
    ds.nk.attrs["long_name"] = "nk"
    ds.nk.attrs["units"] = "[m^-3]"
    ds.nk.attrs["description"] = "Impurity atomic state density"
    ds.ne.attrs["long_name"] = "ne"
    ds.ne.attrs["units"] = "[m^-3]"
    ds.ne.attrs["description"] = "Electron density"

    # Te
    ds.Te.attrs["long_name"] = "Te"
    ds.Te.attrs["units"] = "[eV]"
    ds.Te.attrs["description"] = "Electron temperature"

    # fe
    ds.fe.attrs["long_name"] = "fe"
    ds.fe.attrs["units"] = "[m^-6 s^-3]"
    ds.fe.attrs["description"] = "Electron velocity distribution (isotropic part)"

    # M
    ds.M.attrs["long_name"] = "M"
    ds.M.attrs["units"] = "[s^-1]"
    ds.M.attrs["description"] = "Rate matrices"

    return ds


def add_coordinate_info(ds: xr.Dataset) -> xr.Dataset:
    """Add information on coordinates (x, v, k) to output dataset

    :param ds: Xarray dataset built from SIKERun
    :return: Modified xarray dataset
    """

    ds.x.attrs["long_name"] = "x"
    ds.x.attrs["units"] = "[m]"
    ds.x.attrs["description"] = "Spatial coordinate"

    ds.k.attrs["long_name"] = "k"
    ds.k.attrs["units"] = "N/A"
    ds.k.attrs["description"] = (
        "Atomic state index (vertical index in the case of the rate matrices)"
    )

    ds.j.attrs["long_name"] = "j"
    ds.j.attrs["units"] = "N/A"
    ds.j.attrs["description"] = "Horizontal atomic state index in the rate matrices"

    ds.v.attrs["long_name"] = "v"
    ds.v.attrs["units"] = "[m/s]"
    ds.v.attrs["description"] = "Velocity coordinate"

    ds.i.attrs["long_name"] = "i"
    ds.i.attrs["units"] = "N/A"
    ds.i.attrs["description"] = "Transition ID"

    return ds