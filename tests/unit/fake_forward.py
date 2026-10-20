"""A small stand-in for Forward's access package, in the shape the real one has.

The tests never carry Forward's own table. They build a checkout of a few invented
operations in the same layout, so the code that reads the real source is exercised
without any of its data. The role rules the tests check (each role adds to the one
below it, an org admin holds everything) come from how that code is built, not
from this data.
"""

from __future__ import annotations

from pathlib import Path

from forward_sdk.rbac import ACCESS_DIR

NETWORK_OPERATION = """
public enum NetworkOperation implements LicensableOperation {
    /// View this network and its snapshots
    VIEW_NETWORK_AND_SNAPSHOTS(AccessType.READ, LicenseFacet.BASE),

    /// Use the query language
    USE_NQE(AccessType.READ, LicenseFacet.BASE),

    /// Create a workspace
    CREATE_WORKSPACE(AccessType.WRITE, LicenseFacet.BASE),

    /// Trigger or cancel a snapshot collection
    TRIGGER_SNAPSHOT_COLLECTION(AccessType.WRITE, LicenseFacet.BASE),

    /// Manage change sets
    MANAGE_CHANGE_SETS(AccessType.WRITE, LicenseFacet.PREDICT),

    EDIT_NETWORK_SETTINGS(AccessType.WRITE, LicenseFacet.BASE);
}
"""

ORG_OPERATION = """
public enum OrgOperation implements LicensableOperation {
    /// View data files
    VIEW_DATA_FILES(AccessType.READ, LicenseFacet.NETWORK_OR_SECURITY),

    /// Manage data files
    MANAGE_DATA_FILES(AccessType.WRITE, LicenseFacet.NETWORK),

    /// Manage user accounts
    MANAGE_USER_ACCOUNTS(AccessType.WRITE, LicenseFacet.NO_REQUIREMENT);
}
"""

NETWORK_ROLE = """
public enum NetworkRole implements HasId<NetworkRole> {
    LIMITED_READ_ONLY(
        6,
        Sets.immutableEnumSet(OrgOperation.VIEW_DATA_FILES),
        Sets.immutableEnumSet(NetworkOperation.VIEW_NETWORK_AND_SNAPSHOTS)
    ),
    READ_ONLY(
        3,
        Sets.immutableEnumSet(
            Iterables.concat(LIMITED_READ_ONLY.allowedOrgOperations, EnumSet.of())
        ),
        Sets.immutableEnumSet(
            Iterables.concat(
                LIMITED_READ_ONLY.allowedOperations,
                EnumSet.of(NetworkOperation.USE_NQE)
            )
        )
    ),
    OPERATOR(
        5,
        Sets.immutableEnumSet(READ_ONLY.allowedOrgOperations),
        Sets.immutableEnumSet(
            Iterables.concat(
                READ_ONLY.allowedOperations,
                EnumSet.of(NetworkOperation.CREATE_WORKSPACE, NetworkOperation.MANAGE_CHANGE_SETS)
            )
        )
    ),
    WORKSPACE_OPERATOR(
        7,
        Sets.immutableEnumSet(OPERATOR.allowedOrgOperations),
        Sets.immutableEnumSet(
            Iterables.concat(
                OPERATOR.allowedOperations,
                EnumSet.of(
                    NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION // a note
                )
            )
        )
    ),
    ADMIN(
        2,
        Sets.immutableEnumSet(
            Iterables.concat(
                WORKSPACE_OPERATOR.allowedOrgOperations,
                EnumSet.of(OrgOperation.MANAGE_DATA_FILES)
            )
        ),
        Sets.immutableEnumSet(
            Iterables.concat(
                WORKSPACE_OPERATOR.allowedOperations,
                EnumSet.of(NetworkOperation.EDIT_NETWORK_SETTINGS)
            )
        )
    );

    public static final ImmutableSet<NetworkRole> VALUES = Sets.immutableEnumSet(
        Arrays.asList(values())
    );
}
"""


def write_checkout(root: Path) -> Path:
    """Lay the three files out where a Forward checkout keeps them."""
    access = root / ACCESS_DIR
    access.mkdir(parents=True)
    (access / "NetworkOperation.java").write_text(NETWORK_OPERATION)
    (access / "OrgOperation.java").write_text(ORG_OPERATION)
    (access / "NetworkRole.java").write_text(NETWORK_ROLE)
    return root
