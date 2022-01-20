import sys
import os
import warnings
import subprocess
import json
import shlex
from pathlib import Path

import ase.io
from ase.stress import voigt_6_to_full_3x3_stress

from wfl.configset import ConfigSet_in
from .utils import get_RemoteInfo

from expyre import ExPyRe
import wfl.scripts

def fit(fitting_configs, ACE_name, params, ref_property_prefix='REF_',
        skip_if_present=False, run_dir='.',
        formats = ['json', 'yace'], ace_fit_exec=str((Path(wfl.scripts.__file__).parent / 'ace_fit.jl').resolve()), dry_run=False,
        verbose=True, remote_info=None, wait_for_results=True):
    """Runs ace_fit on a a set of fitting configs

    Parameters
    ----------
    fitting_configs: ConfigSet_in
        set of configurations to fit
    ACE_name: str
        name of ACE potential, saved into files with yace and json suffixes
    params: dict
        dict of keys to turn into command line for ace_fit. each key will be prepended by
        '-' (single characer) or '--' (longer name)
    ref_property_prefix: str, default 'REF\_'
        string prefix added to atoms.info/arrays keys (energy, forces, virial, stress)
    skip_if_present: bool, default False
        skip fitting if output is already present
    run_dir: str, default '.'
        directory to run in
    formats: list(str), default ['json', 'yace']
        list of filename suffix formats to write potentials in
    ace_fit_exec: str, default "wfl/scripts/ace_fit.jl"
        executable for ace_fit
    dry_run: bool, default False
        do a dry run, which returns the matrix size, rather than the potential name
    verbose: bool, default True
        print verbose output
    remote_info: dict or wfl.pipeline.utils.RemoteInfo, or '_IGNORE' or None
        If present and not None and not '_IGNORE', RemoteInfo or dict with kwargs for RemoteInfo
        constructor which triggers running job in separately queued job on remote machine.  If None,
        will try to use env var WFL_ACE_FIT_REMOTEINFO used (see below). '_IGNORE' is for
        internal use, to ensure that remotely running job does not itself attempt to spawn another
        remotely running job.
    wait_for_results: bool, default True
        wait for results of remotely executed job, otherwise return after starting job

    Returns
    -------
    ace_file_base: Path
        path to base of output files, with various suffixes (in formats) added, as well as other files
        generated by ace_fit.jl

    Environment Variables
    ---------------------
    WFL_ACE_FIT_REMOTEINFO: JSON dict or name of file containing JSON with kwargs for RemoteInfo
        contructor to be used to run fitting in separate queued job
    ACE_FIT_JULIA_THREADS: used to set JULIA_NUM_THREADS for ace_fit.jl, which will use julia multithreading (LSQ assembly)
    ACE_FIT_BLAS_THREADS: used by ace_fit.jl for number of threads to set for BLAS multithreading in ace_fit
    """
    assert isinstance(ref_property_prefix, str) and len(ref_property_prefix) > 0

    run_dir = Path(run_dir)
    ace_file_base = run_dir / ACE_name

    def _read_size(file_base):
        with open(file_base.parent / (file_base.name + '.size')) as fin:
            fields = fin.readline().strip().split()
            return (int(fields[3]), int(fields[5]))

    def _check_output_files(file_base, formats):
        for fmt in formats:
            with open(file_base.parent / (file_base.name + fmt)) as fin:
                if fmt == '.json':
                    _ = json.load(fin)
                elif fmt == '.yace':
                    warnings.warn('Cannot parse yace format (can contain dicts with list keys), so just checking its existence')
                    # _ = yaml.safe_load(fin)
                else:
                    raise ValueError(f'Cannot parse ACE file with format {fmt}')

    # prefix each format by '.' if not there yet
    formats = [('' if fmt.startswith('.') else '.') + fmt for fmt in formats]

    # return early if fit calculations are done and output files are present (and readable, when that's
    # possible to check)
    if skip_if_present:
        try:
            if dry_run:
                return _read_size(ace_file_base)

            _check_output_files(ace_file_base, formats)

            return str(ace_file_base)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            # continue below for actual size calculation or fitting
            pass

    remote_info = get_RemoteInfo(remote_info, 'WFL_ACE_FIT_REMOTEINFO')
    if remote_info is not None and remote_info != '_IGNORE':
        input_files = remote_info.input_files.copy()
        output_files = remote_info.output_files.copy()

        # put configs in memory so they can be staged out easily
        fitting_configs = fitting_configs.in_memory()

        # run dir will contain only things created by fitting, so it's safe to copy the
        # entire thing back as output
        output_files.append(str(run_dir))

        xpr = ExPyRe(name=remote_info.job_name, pre_run_commands=remote_info.pre_cmds, post_run_commands=remote_info.post_cmds,
                      env_vars=remote_info.env_vars, input_files=input_files, output_files=output_files, function=fit,
                      kwargs= {'fitting_configs': fitting_configs, 'ACE_name' : ACE_name, 'params': params,
                               'ref_property_prefix': ref_property_prefix,
                               'run_dir': str(run_dir), 'formats': formats, 'ace_fit_exec': ace_fit_exec,
                               'dry_run': dry_run, 'verbose': verbose, 'remote_info': '_IGNORE'})

        xpr.start(resources=remote_info.resources, system_name=remote_info.sys_name, header_extra=remote_info.header_extra,
                  exact_fit=remote_info.exact_fit, partial_node=remote_info.partial_node)

        if not wait_for_results:
            return None
        results, stdout, stderr = xpr.get_results(timeout=remote_info.timeout, check_interval=remote_info.check_interval)

        sys.stdout.write(stdout)
        sys.stderr.write(stderr)

        # no outputs to rename since everything should be in run_dir
        xpr.mark_processed()

        return results

    if not run_dir.exists():
        run_dir.mkdir(parents=True)

    use_params = dict(params)

    if dry_run:
        use_params['dry_run'] = None

    # the code below needs to know an unfortunate amount about the inner workings of ace_fit.jl
    # here we rely on knowledge of the --outfile_base
    use_params['outfile_base'] = str(ace_file_base)
    use_params['outfile_format'] = formats

    use_params['key'] = [ [k, ref_property_prefix + v] for k,v in [('E', 'energy'), ('F', 'forces'), ('V', 'virial')] ]
    use_params['_repeat_key'] = True

    # configs need to be in memory so they can be modified with stress -> virial, and safest to
    # have them as a list (rather than using ConfigSet_in.to_memory()) when passing to ase.io.write below
    fitting_configs = list(fitting_configs)

    # calculate virial from stress, since ASE uses stress but ace_fit.jl only knows about virial
    for at in fitting_configs:
        if ref_property_prefix + 'stress' in at.info:
            stress = at.info[ref_property_prefix + 'stress']
            if len(stress.shape) == 2:
                # 3x3
                at.info[ref_property_prefix + 'virial'] = list((-stress * at.get_volume()).ravel())
            else:
                # Voigt 6-vector
                at.info[ref_property_prefix + 'virial'] = list((-voigt_6_to_full_3x3_stress(stress) * at.get_volume()).ravel())

    # write to file since that's what script needs as input
    fitting_configs_filename = str(run_dir / f'fitting_database.{ACE_name}.extxyz')
    ase.io.write(fitting_configs_filename, fitting_configs)

    use_params["atoms_filename"] = fitting_configs_filename

    fitting_line = dict_to_ace_fit_string(use_params)

    cmd = f'{ace_fit_exec} {fitting_line} > {ace_file_base}.stdout 2> {ace_file_base}.stderr'

    if verbose:
        print('fitting command:\n', cmd)

    orig_julia_num_threads = (os.environ.get('JULIA_NUM_THREADS', None))
    if 'ACE_FIT_JULIA_THREADS' in os.environ:
        os.environ['JULIA_NUM_THREADS'] = os.environ['ACE_FIT_JULIA_THREADS']

    # this will raise an error if return status is not 0
    # we could also capture stdout and stderr here, but right now that's done by shell
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        with open(ace_file_base.parent / (ace_file_base.name + '.stdout')) as fin:
            for l in fin:
                print('STDOUT', l, end='')

        with open(ace_file_base.parent / (ace_file_base.name + '.stderr')) as fin:
            for l in fin:
                print('STDERR', l, end='')

        print(f"Failure in calling ACE fitting script {ace_fit_exec} with error code:", e.returncode)
        raise e

    # repeat output and error
    with open(ace_file_base.parent / (ace_file_base.name + '.stdout')) as fin:
        for l in fin:
            print('STDOUT', l, end='')

    with open(ace_file_base.parent / (ace_file_base.name + '.stderr')) as fin:
        for l in fin:
            print('STDERR', l, end='')

    if dry_run:
        return _read_size(ace_file_base)

    # run can fail without raising an exception in subprocess.run, at least make sure that
    # ACE files exist and are readable
    _check_output_files(ace_file_base, formats)

    if orig_julia_num_threads is not None:
        os.environ['JULIA_NUM_THREADS'] = orig_julia_num_threads
    else:
        try:
            del os.environ['JULIA_NUM_THREADS']
        except KeyError:
            pass

    return ace_file_base


def dict_to_ace_fit_string(param_dict):
    """converts dictionary with ace_fit parameters to string for calling ace_fit.

    Parameters
    ----------
    param_dict: Dict
        dict with command line argument keys and value values

    asserts that mandatory parameters are given

    Returns
    -------
    string for command line with key-val pairs transformed as

        - if "_repeat_"+key exists and is True, key must have iterable val, and will be converted into repeated instances of --key val
        - val is iterable but not dict ->  --key json.dumps(v1) json.dumps(v2) json.dumps(v3) ...
        - val is str ->  --key shlex.quote(val)
        - otherwise -> --key json.dumps(val)

    all json values are also shell-quoted with shlex.quote()
    """

    assert 'atoms_filename' in param_dict.keys()
    assert 'outfile_base' in param_dict.keys()

    def _to_str(val):
        if isinstance(val, str):
            # don't JSON encode strings, that adds an extra layer of quotes
            return shlex.quote(val)
        else:
            # JSON encoding simple types leaves them essentially unchanged, and (presumably)
            # properly encodes more complex types
            return shlex.quote(json.dumps(val))

    def _val_to_ace_fit_string(val):
        if val is None:
            return ''
        if isinstance(val, (str, dict)):
            return _to_str(val)
        # other iterables
        try:
            # join with spaces, appropriately converting content
            return ' '.join([_to_str(v) for v in val])
        except TypeError:
            # not iterable, JSON whole thing
            return _to_str(val)

    ace_fit_string = ''
    for k, val in param_dict.items():
        if k.startswith('_repeat_'):
            continue
        if not param_dict.get('_repeat_' + k, False):
            val = [val]
        for v in val:
            ace_fit_string += ' ' + ('-' if len(k) == 1 else '--') + k + ' ' + _val_to_ace_fit_string(v)

    return ace_fit_string
