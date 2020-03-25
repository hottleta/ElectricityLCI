# -*- coding: utf-8 -*-
import pandas as pd
from electricitylci.globals import output_dir, data_dir
import electricitylci.generation as gen
import electricitylci.import_impacts as import_impacts
from electricitylci.model_config import (
    eia_gen_year,
    keep_mixed_plant_category,
    min_plant_percent_generation_from_primary_fuel_category,
)
import logging

# I added this section to populate a ba_codes variable that could be used
# by other modules without having to re-read the excel files. The purpose
# is to try and provide a common source for balancing authority names, as well
# as FERC an EIA region names.
module_logger = logging.getLogger("combinator.py")
ba_codes = pd.concat(
    [
        pd.read_excel(
            f"{data_dir}/BA_Codes_930.xlsx", header=4, sheet_name="US"
        ),
        pd.read_excel(
            f"{data_dir}/BA_Codes_930.xlsx", header=4, sheet_name="Canada"
        ),
    ]
)
ba_codes.rename(
    columns={
        "etag ID": "BA_Acronym",
        "Entity Name": "BA_Name",
        "NCR_ID#": "NRC_ID",
        "Region": "Region",
    },
    inplace=True,
)
ba_codes.set_index("BA_Acronym", inplace=True)


def fill_nans(df, key_column="FacilityID", target_columns=[], dropna=True):
    """Fills nan values for the specified target columns by using the data from
    other rows, using the key_column for matches. There is an extra step
    to fill remaining nans for the state column because the module to calculate
    transmission and distribution losses needs values in the state column to
    work.

    Parameters
    ----------
    df : dataframe
        Dataframe containing nans and at a minimum the columns key_column and
        target_columns
    key_column : str, optional
        The column to match for the data to fill target_columns, by default "FacilityID"
    target_columns : list, optional
        A list of columns with nans to fill, by default []. If empty, the function
        will use a pre-defined set of columns.
    dropna : bool, optional
        After nans are filled, drop rows that still contain nans in the
        target columns, by default True

    Returns
    -------
    dataframe: hopefully with all of the nans filled.
    """
    from electricitylci.eia860_facilities import eia860_balancing_authority

    if not target_columns:
        target_columns = [
            "Balancing Authority Code",
            "Balancing Authority Name",
            "FuelCategory",
            "NERC",
            "PercentGenerationfromDesignatedFuelCategory",
            "eGRID_ID",
            "Subregion",
            "FERC_Region",
            "EIA_Region",
            "State",
            "Electricity",
        ]
    confirmed_target = []
    for x in target_columns:
        if x in df.columns:
            confirmed_target.append(x)
        else:
            module_logger.warning(f"Column {x} is not in the dataframe")
    if key_column not in df.columns:
        module_logger.warning(
            f"Key column '{key_column}' is not in the dataframe"
        )
        raise KeyError
    #    key_df = (
    #        df[[key_column] + target_columns]
    #        .drop_duplicates(subset=key_column)
    #        .set_index(key_column)
    #    )
    for col in confirmed_target:
        key_df = (
            df[[key_column, col]]
            .dropna()
            .drop_duplicates(subset=key_column)
            .set_index(key_column)
        )
        df.loc[df[col].isnull(), col] = df.loc[
            df[col].isnull(), key_column
        ].map(key_df[col])
    plant_ba = eia860_balancing_authority(eia_gen_year).set_index("Plant Id")
    plant_ba.index = plant_ba.index.astype(int)
    if "State" not in df.columns:
        df["State"] = float("nan")
        confirmed_target.append("State")
    df.loc[df["State"].isna(), "State"] = df.loc[
        df["State"].isna(), "eGRID_ID"
    ].map(plant_ba["State"])
    if dropna:
        df.dropna(subset=confirmed_target, inplace=True)
    return df


def concat_map_upstream_databases(*arg, **kwargs):
    import fedelemflowlist as fedefl

    """
    Concatenates all of the databases given as args. Then all of the
    emissions in the combined database are mapped to the federal elementary
    flows list based on the mapping file 'eLCI' in preparation for being 
    turned into openLCA processes and combined with the generation emissions.

    Parameters
    ----------
    *arg : dataframes
        The dataframes to be combined, generated by the upstream modules or 
        renewables modules (electricitylci.nuclear_upstream, .petroleum_upstream,
        .solar_upstream, etc.)

    Returns
    -------
    datafame
    
    if kwarg group_name is used then the function will return a tuple containing
    the mapped dataframe and lists of tuples for the unique mapped and unmapped flows.
    """
    mapped_column_dict = {
        "TargetFlowName": "FlowName",
        "TargetFlowUUID": "FlowUUID",
        "TargetFlowContext": "Compartment",
        "TargetUnit": "Unit",
    }
    compartment_mapping = {
        "air": "emission/air",
        "water": "emission/water",
        "ground": "emission/ground",
        "soil": "emission/ground",
        "resource": "resource",
        "NETL database/emissions": "NETL database/emissions",
        "NETL database/resources": "NETL database/resources",
    }
    print(f"Concatenating and flow-mapping {len(arg)} upstream databases.")
    upstream_df_list = list()
    for df in arg:
        if isinstance(df, pd.DataFrame):
            if "Compartment_path" not in df.columns:
                df["Compartment_path"] = float("nan")
                df["Compartment_path"].fillna(
                        df["Compartment"].map(compartment_mapping), inplace=True
                        )
            upstream_df_list.append(df)
    upstream_df = pd.concat(upstream_df_list, ignore_index=True, sort=False)
    module_logger.info("Creating flow mapping database")
    flow_mapping = fedefl.get_flowmapping('eLCI')
    flow_mapping["SourceFlowName"] = flow_mapping["SourceFlowName"].str.lower()

    module_logger.info("Preparing upstream df for merge")
    upstream_df["FlowName_orig"] = upstream_df["FlowName"]
    upstream_df["Compartment_orig"] = upstream_df["Compartment"]
    upstream_df["Compartment_path_orig"] = upstream_df["Compartment_path"]
    upstream_df["Unit_orig"] = upstream_df["Unit"]
    upstream_df["FlowName"] = upstream_df["FlowName"].str.lower().str.rstrip()
    upstream_df["Compartment"] = (
        upstream_df["Compartment"].str.lower().str.rstrip()
    )
    upstream_df["Compartment_path"] = (
        upstream_df["Compartment_path"].str.lower().str.rstrip()
    )
    upstream_columns=upstream_df.columns
    groupby_cols = [
        "fuel_type",
        "stage_code",
        "FlowName",
        "Compartment",
        "input",
        "plant_id",
        "Compartment_path",
        "Unit",
        "FlowName_orig",
        "Compartment_path_orig",
        "Unit_orig",
    ]
    upstream_df["Unit"].fillna("<blank>", inplace=True)
    module_logger.info("Grouping upstream database")
    if "Electricity" in upstream_df.columns:
        upstream_df_grp = upstream_df.groupby(
            groupby_cols, as_index=False
        ).agg({"FlowAmount": "sum", "quantity": "mean", "Electricity": "mean"})
    else:
        upstream_df_grp = upstream_df.groupby(
            groupby_cols, as_index=False
        ).agg({"FlowAmount": "sum", "quantity": "mean"})
    upstream_df=upstream_df[["FlowName_orig", "Compartment_path_orig","stage_code"]]
    module_logger.info("Merging upstream database and flow mapping")
    upstream_mapped_df = pd.merge(
        left=upstream_df_grp,
        right=flow_mapping,
        left_on=["FlowName", "Compartment_path"],
        right_on=["SourceFlowName", "SourceFlowContext"],
        how="left",
    )
    del(upstream_df_grp,flow_mapping)
    upstream_mapped_df.drop(
        columns={"FlowName", "Compartment", "Unit"}, inplace=True
    )
    upstream_mapped_df = upstream_mapped_df.rename(
        columns=mapped_column_dict, copy=False
    )
    upstream_mapped_df.drop_duplicates(
        subset=["plant_id", "FlowName", "Compartment_path", "FlowAmount"],
        inplace=True,
    )
    upstream_mapped_df.dropna(subset=["FlowName"], inplace=True)
    #upstream_mapped_df.to_csv(f"{output_dir}/upstream_mapped_df.csv")

    module_logger.info("Applying conversion factors")
    upstream_mapped_df["FlowAmount"]=(upstream_mapped_df["FlowAmount"]*
                                       upstream_mapped_df["ConversionFactor"])
    upstream_mapped_df.rename(
        columns={"fuel_type": "FuelCategory"}, inplace=True
    )
    upstream_mapped_df["FuelCategory"] = upstream_mapped_df[
        "FuelCategory"
    ].str.upper()
    upstream_mapped_df["ElementaryFlowPrimeContext"] = "emission"
    upstream_mapped_df.loc[
        upstream_mapped_df["Compartment"].str.contains("resource"),
        "ElementaryFlowPrimeContext",
    ] = "resource"
    upstream_mapped_df["Source"] = "netl"
    upstream_mapped_df["Year"] = eia_gen_year
    final_columns = [
        "plant_id",
        "FuelCategory",
        "stage_code",
        "FlowName",
        "Compartment",
        "Compartment_path",
        "FlowUUID",
        "Unit",
        "ElementaryFlowPrimeContext",
        "FlowAmount",
        "quantity",
        #            "Electricity",
        "Source",
        "Year",
    ]
    if "Electricity" in upstream_columns:
        final_columns = final_columns + ["Electricity"]
    if "input" in upstream_columns:
        final_columns = final_columns+["input"]

    # I added the section below to help generate lists of matched and unmatched
    # flows. Because of the groupby, it's expensive enough not to run everytime.
    # I didn't want to get rid of it in case it comes in handy later.
    if kwargs != {}:
        if "group_name" in kwargs:
            module_logger.info("kwarg group_name used: generating flows lists")
            unique_orig = upstream_df.groupby(
                by=["FlowName_orig", "Compartment_path_orig"]
            ).groups
            unique_mapped = upstream_mapped_df.groupby(
                by=[
                    "FlowName_orig",
                    "Compartment_path_orig",
                    "Unit_orig",
                    "FlowName",
                    "Compartment",
                    "Unit",
                ]
            ).groups
            unique_mapped_set = set(unique_mapped.keys())
            unique_orig_set = set(unique_orig.keys())
            unmatched_list = sorted(list(unique_orig_set - unique_mapped_set))
            matched_list = sorted(list(unique_mapped_set))
            fname_append = f"_{kwargs['group_name']}"
            with open(
                f"{output_dir}/flowmapping_lists{fname_append}.txt", "w"
            ) as f:
                f.write("Unmatched flows\n")
                if kwargs is not None:
                    if kwargs["group_name"] is not None:
                        f.write(f"From the group: {kwargs['group_name']}\n")
                for x in unmatched_list:
                    f.write(f"{x}\n")
                f.write("\nMatched flows\n")
                for x in matched_list:
                    f.write(f"{x}\n")
                f.close()
            upstream_mapped_df = upstream_mapped_df[final_columns]
            return upstream_mapped_df, unmatched_list, matched_list
    upstream_mapped_df = upstream_mapped_df[final_columns]
    return upstream_mapped_df


def concat_clean_upstream_and_plant(pl_df, up_df):
    """
    Combined the upstream and the generator (power plant) databases followed
    by some database cleanup

    Parameters
    ----------
    pl_df : dataframe
        The generator dataframe, generated by electricitylci.generation
        
    up_df : dataframe
        The combined upstream dataframe.

    Returns
    -------
    dataframe
    """
    region_cols = [
        "NERC",
        "Balancing Authority Code",
        "Balancing Authority Name",
        "Subregion",
    ]

    up_df = up_df.merge(
        right=pl_df[["eGRID_ID"] + region_cols].drop_duplicates(),
        left_on="plant_id",
        right_on="eGRID_ID",
        how="left",
    )
    #    up_df.dropna(subset=region_cols + ["Electricity"], inplace=True)
    combined_df = pd.concat([pl_df, up_df], ignore_index=True)
    combined_df["Balancing Authority Name"] = combined_df[
        "Balancing Authority Code"
    ].map(ba_codes["BA_Name"])
    combined_df["FERC_Region"] = combined_df["Balancing Authority Code"].map(
        ba_codes["FERC_Region"]
    )
    combined_df["EIA_Region"] = combined_df["Balancing Authority Code"].map(
        ba_codes["EIA_Region"]
    )
    categories_to_delete = [
        "plant_id",
        "FuelCategory_right",
        "Net Generation (MWh)",
        "PrimaryFuel_right",
    ]
    for x in categories_to_delete:
        try:
            combined_df.drop(columns=[x], inplace=True)
        except KeyError:
            module_logger.warning(f"Error deleting column {x}")
    combined_df["FacilityID"] = combined_df["eGRID_ID"]
    # I think without the following, given the way the data is created for fuels,
    # there are too many instances where fuel demand can be created when no emissions
    # are reported for the power plant. This should force the presence of a power plant
    # in the dataset for a fuel input to be counted.
    combined_df.loc[
        ~(combined_df["stage_code"] == "Power plant"), "FuelCategory"
    ] = float("nan")
    # This allows construction impacts to be aligned to a power plant type -
    # not as import in openLCA but for analyzing results outside of openLCA.
    combined_df.loc[
        combined_df["FuelCategory"] == "CONSTRUCTION", "FuelCategory"
    ] = float("nan")
    combined_df = fill_nans(combined_df)
    # The hard-coded cutoff is a workaround for now. Changing the parameter
    # to 0 in the config file allowed the inventory to be kept for generators
    # that are now being tagged as mixed.
    generation_filter = (
        combined_df["PercentGenerationfromDesignatedFuelCategory"]
        < min_plant_percent_generation_from_primary_fuel_category / 100
    )
    if keep_mixed_plant_category:
        combined_df.loc[generation_filter, "FuelCategory"] = "MIXED"
        combined_df.loc[generation_filter, "PrimaryFuel"] = "Mixed Fuel Type"
    else:
        combined_df = combined_df.loc[~generation_filter]
    return combined_df


def add_fuel_inputs(gen_df, upstream_df, upstream_dict):
    """
    Converts the upstream emissions database to fuel inputs and adds them
    to the generator dataframe. This is in preparation of generating unit
    processes for openLCA.
    Parameters
    ----------
    gen_df : dataframe
        The generator df containing power plant emissions.
    upstream_df : dataframe
        The combined upstream dataframe.
    upstream_dict : dictionary
        This is the dictionary of upstream "unit processes" as generated by
        electricitylci.upstream_dict after the upstream_dict has been written
        to json-ld. This is important because the uuids for the upstream
        "unit processes" are only generated when written to json-ld.

    Returns
    -------
    dataframe
    """
    from electricitylci.generation import (
        add_technological_correlation_score,
        add_temporal_correlation_score,
    )

    upstream_reduced = upstream_df.drop_duplicates(
        subset=["plant_id", "stage_code", "quantity"]
    )
    fuel_df = pd.DataFrame(columns=gen_df.columns)
    # The upstream reduced should only have one instance of each plant/stage code
    # combination. We'll first map the upstream dictionary to each plant
    # and then expand that dictionary into columns we can use. The goal is
    # to generate the fuels and associated metadata with each plant. That will
    # then be merged with the generation database.
    fuel_df["flowdict"] = upstream_reduced["stage_code"].map(upstream_dict)

    expand_fuel_df = fuel_df["flowdict"].apply(pd.Series)
    fuel_df.drop(columns=["flowdict"], inplace=True)

    fuel_df["Compartment"] = "input"
    fuel_df["FlowName"] = expand_fuel_df["q_reference_name"]
    fuel_df["stage_code"] = upstream_reduced["stage_code"]
    fuel_df["FlowAmount"] = upstream_reduced["quantity"]
    fuel_df["FlowUUID"] = expand_fuel_df["q_reference_id"]
    fuel_df["Unit"] = expand_fuel_df["q_reference_unit"]
    fuel_df["eGRID_ID"] = upstream_reduced["plant_id"]
    fuel_df["FacilityID"] = upstream_reduced["plant_id"]
    fuel_df["FuelCategory"] = upstream_reduced["FuelCategory"]
    fuel_df["Year"] = upstream_reduced["Year"]
    merge_cols = [
        "Age",
        "Balancing Authority Code",
        "Balancing Authority Name",
        "Electricity",
        #        "FRS_ID",
        "NERC",
        "Subregion",
    ]
    fuel_df.drop(columns=merge_cols, inplace=True)
    gen_df_reduced = gen_df[merge_cols + ["eGRID_ID"]].drop_duplicates(
        subset=["eGRID_ID"]
    )

    fuel_df = fuel_df.merge(
        right=gen_df_reduced,
        left_on="eGRID_ID",
        right_on="eGRID_ID",
        how="left",
    )
    fuel_df.dropna(subset=["Electricity"], inplace=True)
    fuel_df["Source"] = "eia"
    fuel_df = add_temporal_correlation_score(fuel_df)
    fuel_df["DataCollection"] = 5
    fuel_df["GeographicalCorrelation"] = 1
    fuel_df["TechnologicalCorrelation"] = 1
    fuel_df["ReliabilityScore"] = 1
    fuel_df["ElementaryFlowPrimeContext"] = "input"
    fuel_cat_key = (
        gen_df[["FacilityID", "FuelCategory"]]
        .drop_duplicates(subset="FacilityID")
        .set_index("FacilityID")
    )
    fuel_df["FuelCategory"] = fuel_df["FacilityID"].map(
        fuel_cat_key["FuelCategory"]
    )
    gen_plus_up_df = pd.concat([gen_df, fuel_df], ignore_index=True)
    gen_plus_up_df = fill_nans(gen_plus_up_df)
    # Taking out anything with New Brunswick System Operator so that
    # these fuel inputs (for a very small US portion of NBSO) don't get mapped
    # to the Canadian import rollup (i.e., double-counted)
    gen_plus_up_df = gen_plus_up_df.loc[
        gen_plus_up_df["Balancing Authority Name"]
        != "New Brunswick System Operator",
        :,
    ].reset_index(drop=True)
    return gen_plus_up_df


if __name__ == "__main__":
    import electricitylci.coal_upstream as coal
    import electricitylci.natural_gas_upstream as ng
    import electricitylci.petroleum_upstream as petro
    import electricitylci.geothermal as geo
    import electricitylci.solar_upstream as solar
    import electricitylci.wind_upstream as wind
    import electricitylci.nuclear_upstream as nuke

    #coal_df = coal.generate_upstream_coal(2016)
    #ng_df = ng.generate_upstream_ng(2016)
    petro_df = petro.generate_petroleum_upstream(2016)
    geo_df = geo.generate_upstream_geo(2016)
    solar_df = solar.generate_upstream_solar(2016)
    wind_df = wind.generate_upstream_wind(2016)
    nuke_df = nuke.generate_upstream_nuc(2016)
    upstream_df = concat_map_upstream_databases(
        petro_df, geo_df, solar_df, wind_df, nuke_df
    )
    plant_df = gen.create_generation_process_df()
    plant_df["stage_code"] = "Power plant"
    print(plant_df.columns)
    print(upstream_df.columns)
    combined_df = concat_clean_upstream_and_plant(plant_df, upstream_df)
    canadian_inventory = import_impacts.generate_canadian_mixes(combined_df)
    combined_df = pd.concat([combined_df, canadian_inventory])
    combined_df.sort_values(
        by=["eGRID_ID", "Compartment", "FlowName", "stage_code"], inplace=True
    )
    combined_df.to_csv(f"{output_dir}/combined_df.csv")
