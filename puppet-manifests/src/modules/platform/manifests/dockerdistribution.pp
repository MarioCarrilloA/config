class platform::dockerdistribution::params (
    $registry_ks_endpoint = undef,
) {}

define platform::dockerdistribution::write_config (
  $registry_readonly = false,
  $file_path = '/etc/docker-distribution/registry/runtime_config.yml',
  $docker_registry_ip = undef,
  $docker_registry_host = undef,
){
  file { $file_path:
    ensure  => present,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => template('platform/dockerdistribution.conf.erb'),
  }
}

class platform::dockerdistribution::config
  inherits ::platform::dockerdistribution::params {
  include ::platform::params
  include ::platform::kubernetes::params

  include ::platform::network::mgmt::params
  include ::platform::docker::params

  $docker_registry_ip = $::platform::network::mgmt::params::controller_address
  $docker_registry_host = $::platform::network::mgmt::params::controller_address_url
  $runtime_config = '/etc/docker-distribution/registry/runtime_config.yml'
  $used_config = '/etc/docker-distribution/registry/config.yml'

  # check insecure registries
  if $::platform::docker::params::insecure_registry {
    # insecure registry is true means unified registry was set
    $insecure_registries = "\"${::platform::docker::params::k8s_registry}\""
  } else {
    $insecure_registries = ''
  }

  # for external docker registry running insecure mode
  file { '/etc/docker':
    ensure => 'directory',
    owner  => 'root',
    group  => 'root',
    mode   => '0700',
  }
  -> file { '/etc/docker/daemon.json':
    ensure  => present,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => template('platform/insecuredockerregistry.conf.erb'),
  }

  platform::dockerdistribution::write_config { 'runtime_config':
    docker_registry_ip   => $docker_registry_ip,
    docker_registry_host => $docker_registry_host
  }

  -> exec { 'use runtime config file':
    command => "ln -fs ${runtime_config} ${used_config}",
  }

  platform::dockerdistribution::write_config { 'readonly_config':
    registry_readonly    => true,
    file_path            => '/etc/docker-distribution/registry/readonly_config.yml',
    docker_registry_ip   => $docker_registry_ip,
    docker_registry_host => $docker_registry_host
  }

  file { '/etc/docker-distribution/registry/token_server.conf':
    ensure  => present,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => template('platform/registry-token-server.conf.erb'),
  }

  # copy the startup script to where it is supposed to be
  file {'docker_distribution_initd_script':
    ensure => 'present',
    path   => '/etc/init.d/docker-distribution',
    mode   => '0755',
    source => "puppet:///modules/${module_name}/docker-distribution"
  }

  file {'registry_token_server_initd_script':
    ensure => 'present',
    path   => '/etc/init.d/registry-token-server',
    mode   => '0755',
    source => "puppet:///modules/${module_name}/registry-token-server"
  }

  # self-signed certificate for registry use
  # this needs to be generated here because the certificate
  # need to know the registry ip address for SANs
  if str2bool($::is_initial_config_primary) {
    $shared_dir = $::platform::params::config_path
    $certs_dir = '/etc/ssl/private'

    # create the certificate files
    file { "${certs_dir}/registry-cert-extfile.cnf":
      ensure  => present,
      owner   => 'root',
      group   => 'root',
      mode    => '0400',
      content => template('platform/registry-cert-extfile.erb'),
    }

    -> exec { 'docker-registry-generate-cert':
      command   => "openssl req -x509 -sha256 -nodes -days 365 -newkey rsa:2048 \
                    -keyout ${certs_dir}/registry-cert.key \
                    -out ${certs_dir}/registry-cert.crt \
                    -config ${certs_dir}/registry-cert-extfile.cnf",
      logoutput => true
    }

    -> exec { 'docker-registry-generate-pkcs1-cert-from-pkcs8':
      command   => "openssl rsa -in ${certs_dir}/registry-cert.key \
                    -out ${certs_dir}/registry-cert-pkcs1.key",
      logoutput => true
    }

    # ensure permissions are set correctly
    -> file { "${certs_dir}/registry-cert-pkcs1.key":
      ensure => 'file',
      owner  => 'root',
      group  => 'root',
      mode   => '0400',
    }

    -> file { "${certs_dir}/registry-cert.key":
      ensure => 'file',
      owner  => 'root',
      group  => 'root',
      mode   => '0400',
    }

    -> file { "${certs_dir}/registry-cert.crt":
      ensure => 'file',
      owner  => 'root',
      group  => 'root',
      mode   => '0400',
    }

    # delete the extfile used in certificate generation
    -> exec { 'remove-registry-cert-extfile':
      command => "rm ${certs_dir}/registry-cert-extfile.cnf"
    }

    # copy certificates and keys to shared directory for second controller
    # we do not need to worry about second controller being up at this point,
    # since we have a is_initial_config_primary check
    -> file { "${shared_dir}/registry-cert-pkcs1.key":
      ensure => 'file',
      owner  => 'root',
      group  => 'root',
      mode   => '0400',
      source => "${certs_dir}/registry-cert-pkcs1.key",
    }

    -> file { "${shared_dir}/registry-cert.key":
      ensure => 'file',
      owner  => 'root',
      group  => 'root',
      mode   => '0400',
      source => "${certs_dir}/registry-cert.key",
    }

    -> file { "${shared_dir}/registry-cert.crt":
      ensure => 'file',
      owner  => 'root',
      group  => 'root',
      mode   => '0400',
      source => "${certs_dir}/registry-cert.crt",
    }

    # copy the certificate to docker certificates directory,
    # which makes docker trust that specific certificate
    # this is required for self-signed and also if the user does
    # not have a certificate signed by a "default" CA

    -> file { '/etc/docker/certs.d':
      ensure => 'directory',
      owner  => 'root',
      group  => 'root',
      mode   => '0700',
    }

    -> file { '/etc/docker/certs.d/registry.local:9001':
      ensure => 'directory',
      owner  => 'root',
      group  => 'root',
      mode   => '0700',
    }

    -> file { '/etc/docker/certs.d/registry.local:9001/registry-cert.crt':
      ensure => 'file',
      owner  => 'root',
      group  => 'root',
      mode   => '0400',
      source => "${certs_dir}/registry-cert.crt",
    }
  }

}

# compute also needs the "insecure" flag in order to deploy images from
# the registry. This is needed for insecure external registry
class platform::dockerdistribution::compute
  inherits ::platform::dockerdistribution::params {
  include ::platform::kubernetes::params

  include ::platform::network::mgmt::params
  include ::platform::docker::params

  # check insecure registries
  if $::platform::docker::params::insecure_registry {
    # insecure registry is true means unified registry was set
    $insecure_registries = "\"${::platform::docker::params::k8s_registry}\""
  } else {
    $insecure_registries = ''
  }

  # for external docker registry running insecure mode
  file { '/etc/docker':
    ensure => 'directory',
    owner  => 'root',
    group  => 'root',
    mode   => '0700',
  }
  -> file { '/etc/docker/daemon.json':
    ensure  => present,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => template('platform/insecuredockerregistry.conf.erb'),
  }
}

class platform::dockerdistribution
  inherits ::platform::dockerdistribution::params {
  include ::platform::kubernetes::params

  include platform::dockerdistribution::config

  Class['::platform::docker::config'] -> Class[$name]
}

class platform::dockerdistribution::reload {
  platform::sm::restart {'registry-token-server': }
  platform::sm::restart {'docker-distribution': }
}

# this does not update the config right now
# the run time is only used to restart the token server and registry
class platform::dockerdistribution::runtime {

  class {'::platform::dockerdistribution::reload':
    stage => post
  }
}

class platform::dockerdistribution::garbagecollect {
  $runtime_config = '/etc/docker-distribution/registry/runtime_config.yml'
  $readonly_config = '/etc/docker-distribution/registry/readonly_config.yml'
  $used_config = '/etc/docker-distribution/registry/config.yml'

  exec { 'turn registry read only':
    command => "ln -fs ${readonly_config} ${used_config}",
  }

  # it doesn't like 2 platform::sm::restart with the same name
  # so we have to do 1 as a command
  -> exec { 'restart docker-distribution in read only':
    command => 'sm-restart-safe service docker-distribution',
  }

  -> exec { 'run garbage collect':
    command => "/usr/bin/registry garbage-collect ${used_config}",
  }

  -> exec { 'turn registry back to read write':
    command => "ln -fs ${runtime_config} ${used_config}",
  }

  -> platform::sm::restart {'docker-distribution': }
}

class platform::dockerdistribution::bootstrap
  inherits ::platform::dockerdistribution::params {

  include platform::dockerdistribution::config
  Class['::platform::docker::config'] -> Class[$name]
}
