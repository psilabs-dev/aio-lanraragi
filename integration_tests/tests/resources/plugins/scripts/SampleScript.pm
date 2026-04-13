package LANraragi::Plugin::Scripts::SampleScript;

use strict;
use warnings;
no warnings 'uninitialized';

# Meta-information about your plugin.
sub plugin_info {

    return (
        # Standard metadata
        name        => "Sample Script",
        type        => "script",
        namespace   => "sample-script",
        author      => "koyomi",
        version     => "1.0",
        description => "Script example",
        oneshot_arg => "Value to echo back"
    );

}

# Mandatory function to be implemented by your script
sub run_script {
    shift;
    my $lrr_info = shift;

    my $arg = $lrr_info->{oneshot_param};

    return ( result => $arg // "no argument provided" );
}

1;
