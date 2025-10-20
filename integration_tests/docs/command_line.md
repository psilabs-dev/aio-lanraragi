# Command Line Documentation

`lrr-staging` is a command line tool which manages LRR in a staging environment, which uses resource prefix "staging_" and port offset 1.

Note: this is experimental and subject to iteration and breaking, regardless of version. Only Docker is supported. In some optimistic future, an aws-cli-like multi-profile configuration may be supported.

Usage is separated to the following:
```sh
lrr-staging version     # get integration tests version
lrr-staging up          # create and start staging environment
lrr-staging restart     # restart environment (requires created)
lrr-staging stop        # stop environment (requires created)
lrr-staging start       # start environment (requires created)
lrr-staging down        # destroy environment
```

`restart`, `stop` and `start` which have no options and are self-explanatory. They are also the ones which will probably break the most.

## `up` options

Create and start staging environment with official Docker image:
```sh
lrr-staging up
```
This will expose LRR at port 3001.

Create and start environment with custom image:
```sh
lrr-staging up --image myusername/customimage
```

Create and start environment with specific git repo (with custom branch if specified):
```sh
lrr-staging up --git-url=https://github.com/difegue/LANraragi.git --git-branch=dev
```

Create and start environment with a local LANraragi project:
```sh
lrr-staging up --build /path/to/LANraragi/project
```

## `down` options

Teardown services (but keep volumes)
```sh
lrr-staging down
```

Teardown everything.
```sh
lrr-staging down [--volumes]
```
