# Image for the OpenPBS cluster (shared by the server and the compute nodes;
# the role is selected at runtime with PBS_ROLE)
# - Built from source on Rocky Linux 9, natively on arm64 (Apple silicon) or x86_64
# - The Python interpreter embedded in PBS (hooks, pbs_python) is Python 3.12
# - v23.06.06 fails to build with Python 3.12, so a commit on the master branch is used

FROM docker.io/library/rockylinux:9

# OpenPBS commit to build, pinned to a tested commit:
#   cd7ab5ed = master commit of 2026-04-10, "Fixed compilation errors and warnings (#2714)"
#   (latest master as of 2026-10-01; tested with Python 3.12 / Rocky Linux 9 / arm64)
# To try another commit or the latest master, override it at build time:
#   e.g. podman compose build --build-arg OPENPBS_REF=master
ARG OPENPBS_REF=cd7ab5edaf03dcac2f1cb9e2f42d75eb117d468a
ENV LANG=C.UTF-8

# --- Build and runtime dependencies ------------------------------------
# On EL9, the crb repository is used instead of powertools
RUN dnf install -y dnf-plugins-core epel-release \
 && dnf config-manager --set-enabled crb \
 && dnf install -y \
      gcc make rpm-build libtool hwloc-devel \
      libX11-devel libXt-devel libedit-devel libical-devel \
      ncurses-devel perl postgresql-devel postgresql-contrib tcl-devel \
      tk-devel swig expat-devel openssl-devel libXext libXft \
      autoconf automake gcc-c++ cjson-devel \
      python3.12 python3.12-devel python3.12-pip \
      expat libedit postgresql-server sudo tcl tk libical chkconfig cjson \
      hwloc-libs procps-ng which hostname findutils \
 && dnf clean all

# --- Directory that makes PBS use Python 3.12 ---------------------------
# Passed to --with-python instead of the system default python3 (3.9)
RUN mkdir -p /opt/pbs-build-python/bin \
 && ln -sf /usr/bin/python3.12        /opt/pbs-build-python/bin/python3 \
 && ln -sf /usr/bin/python3.12-config /opt/pbs-build-python/bin/python3-config \
 && /opt/pbs-build-python/bin/python3 --version

# --- Download, build and install OpenPBS -------------------------------
WORKDIR /build
ADD https://github.com/openpbs/openpbs/archive/${OPENPBS_REF}.tar.gz /build/openpbs.tar.gz
# pipefail makes sure a failure of make is not hidden by "| tee"
# (set inside RUN because the SHELL instruction is ignored in Podman's default OCI format)
#
# Recording the commit that was built:
#   GitHub tarballs are created with git archive, which stores the commit ID in the
#   tar header (pax "comment"). It is written to /opt/pbs/OPENPBS_COMMIT so that the
#   actual commit is known even when OPENPBS_REF is a branch name such as master.
#   Check with: podman exec pbs-server cat /opt/pbs/OPENPBS_COMMIT
RUN set -o pipefail \
 && export PATH=/opt/pbs-build-python/bin:$PATH \
 && tar xzf openpbs.tar.gz --strip-components=1 \
 && ./autogen.sh \
 && ./configure --with-python=/opt/pbs-build-python --prefix=/opt/pbs \
 && make 2>&1 | tee /var/log/pbs_build.log \
 && make install 2>&1 | tee /var/log/pbs_install.log \
 && python3.12 -c "import tarfile; print(tarfile.open('openpbs.tar.gz').pax_headers.get('comment', 'unknown'))" \
      > /opt/pbs/OPENPBS_COMMIT \
 && echo "OpenPBS ref=${OPENPBS_REF} commit=$(cat /opt/pbs/OPENPBS_COMMIT)" \
 && cd / && rm -rf /build

# --- Post-install ------------------------------------------------------
# Note: PBS is NOT started here (the datastore would be created with the build-time hostname)
RUN /opt/pbs/libexec/pbs_postinstall \
 && chmod 4755 /opt/pbs/sbin/pbs_iff /opt/pbs/sbin/pbs_rcp

# --- Open MPI with PBS TM support ----------------------------------------
# --with-tm=/opt/pbs is used instead of Slurm's --with-slurm / --with-pmix
#   - mpirun picks up the allocation in $PBS_NODEFILE automatically
#   - processes on each node are started through PBS (TM), not ssh
#   - the PMIx bundled with Open MPI 4.1 is used (no external PMIx is needed, unlike with Slurm)
ARG OPENMPI_VERSION=4.1.8
ADD https://download.open-mpi.org/release/open-mpi/v4.1/openmpi-${OPENMPI_VERSION}.tar.gz /build-ompi/openmpi.tar.gz
RUN set -euxo pipefail \
 && cd /build-ompi \
 && tar xzf openmpi.tar.gz --strip-components=1 \
 && ./configure --prefix=/opt/openmpi \
                --with-tm=/opt/pbs \
                --enable-mpirun-prefix-by-default \
 && make -j"$(nproc)" \
 && make install \
 && cd / && rm -rf /build-ompi \
 && echo /opt/openmpi/lib > /etc/ld.so.conf.d/openmpi.conf && ldconfig \
 && /opt/openmpi/bin/ompi_info | grep -E 'MCA (plm|ras): tm'

# Make Open MPI available in jobs (login shells) as well
# btl_vader_single_copy_mechanism=none : CMA (ptrace) is not available in containers and causes errors
RUN printf '%s\n' \
      'export PATH=/opt/openmpi/bin:$PATH' \
      'export MANPATH=/opt/openmpi/share/man:${MANPATH:-}' \
      'export OMPI_MCA_btl_vader_single_copy_mechanism=none' \
      > /etc/profile.d/openmpi.sh
ENV PATH=/opt/openmpi/bin:$PATH \
    OMPI_MCA_btl_vader_single_copy_mechanism=none

# mpi4py for python3.12, built against the Open MPI above.
# Installed by root with pip into the system-wide site-packages of python3.12 (under /usr/local),
# so it is available to all users. Check the location with:
#   python3.12 -c 'import mpi4py; print(mpi4py.__file__)'
ARG INSTALL_MPI4PY=1
RUN if [ "$INSTALL_MPI4PY" = 1 ]; then \
      MPICC=/opt/openmpi/bin/mpicc python3.12 -m pip install --no-cache-dir mpi4py ; \
    fi

# Extra Python packages used by hooks (e.g. QRMI) are installed into python3.12 here
#   e.g. podman compose build --build-arg PIP_PACKAGES="qrmi"
ARG PIP_PACKAGES=""
RUN if [ -n "$PIP_PACKAGES" ]; then python3.12 -m pip install --no-cache-dir $PIP_PACKAGES; fi

# Regular user for submitting jobs (PBS rejects qsub from root by default)
RUN useradd -m -u 1000 pbsuser

# Allow pbsuser to use sudo without a password (for a local test cluster only; do not use in production)
#   pbsuser has no password, so without NOPASSWD sudo would stop at the password prompt
#   files in sudoers.d are ignored by sudo unless their mode is 440
RUN echo 'pbsuser ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/pbsuser \
 && chmod 440 /etc/sudoers.d/pbsuser

# --- Development and debugging tools -------------------------------------
# Based on the package list of the Slurm containers, keeping what is useful with PBS.
# Placed after the heavy builds (PBS / Open MPI) to make good use of the build cache.
# Slurm-specific packages are not needed with PBS and are not installed:
#   - munge                          : node authentication for Slurm (PBS uses its own, resvport)
#   - mariadb                        : for slurmdbd accounting (PBS uses its own PostgreSQL)
#   - http-parser / json-c / libyaml : for slurmrestd
#   - lua                            : for Slurm job_submit plugins (PBS hooks in Python are the equivalent)
#
# Installed:
#   editing / inspection : vim-enhanced bash-completion jq less
#   debugging            : gdb valgrind strace
#   networking           : iputils(ping) net-tools(netstat, ifconfig) iproute(ip, ss) bind-utils(dig, host)
#   processes            : psmisc(killall, pstree, fuser)
#   sources / building   : wget git bzip2 gnupg2 cmake flex clang-tools-extra
#   numerics             : openblas-devel (for testing MPI applications)
#   line endings         : dos2unix (fixes CRLF in files edited on Windows)
RUN dnf install -y \
      vim-enhanced bash-completion jq less \
      gdb valgrind strace \
      iputils net-tools iproute bind-utils \
      psmisc \
      wget git bzip2 gnupg2 cmake flex clang-tools-extra \
      openblas-devel \
      dos2unix \
 && dnf clean all

# --- Rust toolchain (rustup) ---------------------------------------------
# With the defaults, rustup installs into root's home (/root/.cargo, /root/.rustup),
# which pbsuser (who runs the jobs) cannot use. Install into /opt/rust, shared by all users.
#   RUSTUP_HOME=/opt/rust/rustup : the toolchains (rustc, cargo, clippy, rustfmt, ...)
#   CARGO_HOME=/opt/rust/cargo   : the commands such as cargo and rustc (bin/)
#   --no-modify-path             : do not edit shell profiles; PATH is set in profile.d below
# CARGO_HOME is not fixed for users, so each user's crate cache goes to the default
# ~/.cargo (on the shared /home), because /opt/rust is owned by root and not writable.
ENV RUSTUP_HOME=/opt/rust/rustup
RUN export CARGO_HOME=/opt/rust/cargo \
 && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path \
 && chmod -R a+rX /opt/rust \
 && /opt/rust/cargo/bin/rustc --version \
 && /opt/rust/cargo/bin/cargo --version \
 && printf '%s\n' \
      'export RUSTUP_HOME=/opt/rust/rustup' \
      'export PATH=/opt/rust/cargo/bin:$PATH' \
      > /etc/profile.d/rust.sh
ENV PATH=/opt/rust/cargo/bin:$PATH

# --- Entrypoint --------------------------------------------------------
# Files edited on Windows (under /mnt/c in WSL, or with Windows editors) may get CRLF line
# endings, and the container then fails to start with errors such as
# "/bin/bash^M: bad interpreter". The trailing CR (\r) is removed with sed after copying
# (files that already use LF are not changed).
# Note: job scripts (*.pbs) placed in shared/ are not part of the image; if they have CRLF
#   line endings, fix them with e.g. `dos2unix hello_mpi.pbs` before running qsub.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod 755 /usr/local/bin/entrypoint.sh

WORKDIR /shared
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
