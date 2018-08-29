import logging as log
import typing
import uuid

import olca
import olca.pack as pack


def write(processes: dict, file_path: str):
    """ Write the given process dictionary to a olca-schema zip file with the
        given path.
    """
    with pack.Writer(file_path) as writer:
        created_ids = set()
        for d in processes.values():
            process = olca.Process()
            process.name = _val(d, 'name')
            category_path = _val(d, 'category')
            process.id = _uid(olca.ModelType.PROCESS,
                              category_path, process.name)
            process.category = _category(
                category_path, olca.ModelType.PROCESS, writer, created_ids)
            process.exchanges = []
            last_id = 0
            for e in _val(d, 'exchanges', default=[]):
                exchange = _exchange(e, writer, created_ids)
                if exchange is not None:
                    last_id += 1
                    exchange.internal_id = last_id
                    process.exchanges.append(exchange)
            writer.write(process)


def _category(path: str, mtype: olca.ModelType, writer: pack.Writer,
              created_ids: set) -> typing.Optional[olca.Ref]:
    if not isinstance(path, str):
        return None
    if path.strip() == '':
        return None
    parts = path.split('/')
    parent = None  # type: olca.Ref
    for i in range(0, len(parts)):
        uid_path = [str(mtype.value)] + parts[0:(i + 1)]
        uid = _uid(*uid_path)
        name = parts[i].strip()
        if uid not in created_ids:
            category = olca.Category()
            category.id = uid
            category.model_type = mtype
            category.name = name
            category.category = parent
            writer.write(category)
            created_ids.add(uid)
        parent = olca.ref(olca.Category, uid, name)
        parent.category_path = uid_path[1:]
    return parent


def _exchange(d: dict, writer: pack.Writer,
              created_ids: set) -> typing.Optional[olca.Exchange]:
    if d is None:
        return None
    exchange = olca.Exchange()
    exchange.input = _val(d, 'input', default=False)
    exchange.quantitative_reference = _val(d, 'quantitativeReference',
                                           default=False)
    exchange.avoided_product = _val(d, 'avoidedProduct', default=False)
    exchange.amount = _val(d, 'amount', default=0.0)
    unit_name = _val(d, 'unit', 'name')
    exchange.unit = _unit(unit_name)
    exchange.flow_property = _flow_property(unit_name)
    return exchange


def _unit(unit_name: str) -> typing.Optional[olca.Ref]:
    """ Get the ID of the openLCA reference unit with the given name. """
    ref_id = None
    if unit_name == 'MWh':
        ref_id = '92e3bd49-8ed5-4885-9db6-fc88c7afcfcb'
    elif unit_name == 'MJ':
        ref_id = '52765a6c-3896-43c2-b2f4-c679acf13efe'
    elif unit_name == 'kg':
        ref_id = '20aadc24-a391-41cf-b340-3e4529f44bde'
    if ref_id is None:
        log.error('unknown unit %s; no unit reference', unit_name)
        return None
    return olca.ref(olca.Unit, ref_id, unit_name)


def _flow_property(unit_name: str) -> typing.Optional[olca.Ref]:
    """ Get the ID of the openLCA reference flow property for the unit with
        the given name.
    """
    if unit_name == 'MWh':
        ref_id = 'f6811440-ee37-11de-8a39-0800200c9a66'
        return olca.ref(olca.FlowProperty, ref_id, 'Energy')
    elif unit_name == 'MJ':
        ref_id = 'f6811440-ee37-11de-8a39-0800200c9a66'
        return olca.ref(olca.FlowProperty, ref_id, 'Energy')
    elif unit_name == 'kg':
        ref_id = '93a60a56-a3c8-11da-a746-0800200b9a66'
        return olca.ref(olca.FlowProperty, ref_id, 'Mass')
    log.error('unknown unit %s; no flow property reference', unit_name)
    return None


def _val(d: dict, *path, **kvargs):
    if d is None or path is None:
        return None
    v = d
    for p in path:
        if not isinstance(v, dict):
            return None
        v = v.get(p)
    if v is None and 'default' in kvargs:
        return kvargs['default']
    return v


def _uid(*args):
    path = '/'.join([str(arg).strip() for arg in args]).lower()
    return str(uuid.uuid3(uuid.NAMESPACE_OID, path))


# this currently just for testing and will be removed later
if __name__ == '__main__':
    import os
    import pickle

    zip_file = '../ElectricityLCI_jsonld.zip'
    if os.path.isfile(zip_file):
        os.remove(zip_file)
    with open('../CAMX_gen_dict_WI.p', 'rb') as f:
        data = pickle.load(f)
        write(data, zip_file)
