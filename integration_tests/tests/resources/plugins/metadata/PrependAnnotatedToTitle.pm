package LANraragi::Plugin::Metadata::Testing::PrependAnnotatedToTitle;

use strict;
use warnings;
use utf8;

sub plugin_info {
    return (
        name        => "PrependAnnotatedToTitle",
        type        => "metadata",
        namespace   => "testannotatetitlemetadata",
        author      => "LANraragi Integration Tests",
        version     => "1.0",
        description => "Prepends 'annotated ' to archive title.",
        parameters  => [],
    );
}

sub get_tags {
    shift;
    my ( $lrr_info, $params ) = @_;

    my $title = $lrr_info->{archive_title} // "";
    return (
        tags  => "",
        title => "annotated " . $title
    );
}

1;
