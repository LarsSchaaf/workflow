.. workflow documentation master file, created by
   sphinx-quickstart on Tue May  4 16:26:48 2021.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

########################################
Welcome to Workflow's documentation!
########################################

A Python toolkit for building interatomic potential creation and atomistic simulation workflows. 

Documentation in progress! 

The main functions of Workflow is to efficiently parallelise operations over a set of atomic configurations. Given an operation that is defined to act a single configuration (e.g. evaluate energy of a structure with CASTEP ASE calculator), Workflow may apply the operation to multiple configurations in parallel. 

The Workflow-specific code structures (e.g. how configurations are handled, mechanism to parallelise operations) are covered in :ref:`Code structure <code_structure>`. Currently implemented self-contained per-configuration operations are sketched out in :ref:`Operations <operations>`. Descriptions of Workflows, built out of these modular operations are described in :ref:`Workflows <workflows>`. There are also descriptions :ref:`command line interface <command_line>`, :ref:`examples <examples>` of common tasks to get started with, some :ref:`guidelines on contributing <contributing>` and a :ref:`Python API <api>`. 


For now the documentation must be build from source. 

1. ``sphinx-apidoc -o source ../wfl``  (only whenever source code changes)
2. ``make html``
3. ``open build/html/index.html``


.. toctree::
    :maxdepth: 3
    :caption: Contents:

    install.rst
    code_structure.rst
    operations.rst
    workflows.rst
    command_line.rst
    examples.rst
    contributing.rst	
    modules.rst

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
