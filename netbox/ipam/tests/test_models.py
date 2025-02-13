from netaddr import IPNetwork, IPSet
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from dcim.models import Interface, Device, DeviceRole, DeviceType, Manufacturer, Site
from ipam.choices import IPAddressRoleChoices, PrefixStatusChoices
from ipam.models import Aggregate, IPAddress, IPRange, Prefix, RIR, VLAN, VLANGroup, VRF, L2VPN, L2VPNTermination


class TestAggregate(TestCase):

    def test_get_utilization(self):
        rir = RIR.objects.create(name='RIR 1', slug='rir-1')
        aggregate = Aggregate(prefix=IPNetwork('10.0.0.0/8'), rir=rir)
        aggregate.save()

        # 25% utilization
        Prefix.objects.bulk_create((
            Prefix(prefix=IPNetwork('10.0.0.0/12')),
            Prefix(prefix=IPNetwork('10.16.0.0/12')),
            Prefix(prefix=IPNetwork('10.32.0.0/12')),
            Prefix(prefix=IPNetwork('10.48.0.0/12')),
        ))
        self.assertEqual(aggregate.get_utilization(), 25)

        # 50% utilization
        Prefix.objects.bulk_create((
            Prefix(prefix=IPNetwork('10.64.0.0/10')),
        ))
        self.assertEqual(aggregate.get_utilization(), 50)

        # 100% utilization
        Prefix.objects.bulk_create((
            Prefix(prefix=IPNetwork('10.128.0.0/9')),
        ))
        self.assertEqual(aggregate.get_utilization(), 100)


class TestPrefix(TestCase):

    def test_get_duplicates(self):
        prefixes = Prefix.objects.bulk_create((
            Prefix(prefix=IPNetwork('192.0.2.0/24')),
            Prefix(prefix=IPNetwork('192.0.2.0/24')),
            Prefix(prefix=IPNetwork('192.0.2.0/24')),
        ))
        duplicate_prefix_pks = [p.pk for p in prefixes[0].get_duplicates()]

        self.assertSetEqual(set(duplicate_prefix_pks), {prefixes[1].pk, prefixes[2].pk})

    def test_get_child_prefixes(self):
        vrfs = VRF.objects.bulk_create((
            VRF(name='VRF 1'),
            VRF(name='VRF 2'),
            VRF(name='VRF 3'),
        ))
        prefixes = Prefix.objects.bulk_create((
            Prefix(prefix=IPNetwork('10.0.0.0/16'), status=PrefixStatusChoices.STATUS_CONTAINER),
            Prefix(prefix=IPNetwork('10.0.0.0/24'), vrf=None),
            Prefix(prefix=IPNetwork('10.0.1.0/24'), vrf=vrfs[0]),
            Prefix(prefix=IPNetwork('10.0.2.0/24'), vrf=vrfs[1]),
            Prefix(prefix=IPNetwork('10.0.3.0/24'), vrf=vrfs[2]),
        ))
        child_prefix_pks = {p.pk for p in prefixes[0].get_child_prefixes()}

        # Global container should return all children
        self.assertSetEqual(child_prefix_pks, {prefixes[1].pk, prefixes[2].pk, prefixes[3].pk, prefixes[4].pk})

        prefixes[0].vrf = vrfs[0]
        prefixes[0].save()
        child_prefix_pks = {p.pk for p in prefixes[0].get_child_prefixes()}

        # VRF container is limited to its own VRF
        self.assertSetEqual(child_prefix_pks, {prefixes[2].pk})

    def test_get_child_ranges(self):
        prefix = Prefix(prefix='192.168.0.16/28')
        prefix.save()
        ranges = IPRange.objects.bulk_create((
            IPRange(start_address=IPNetwork('192.168.0.1/24'), end_address=IPNetwork('192.168.0.10/24'), size=10),  # No overlap
            IPRange(start_address=IPNetwork('192.168.0.11/24'), end_address=IPNetwork('192.168.0.17/24'), size=7),  # Partial overlap
            IPRange(start_address=IPNetwork('192.168.0.18/24'), end_address=IPNetwork('192.168.0.23/24'), size=6),  # Full overlap
            IPRange(start_address=IPNetwork('192.168.0.24/24'), end_address=IPNetwork('192.168.0.30/24'), size=7),  # Full overlap
            IPRange(start_address=IPNetwork('192.168.0.31/24'), end_address=IPNetwork('192.168.0.40/24'), size=10),  # Partial overlap
        ))

        child_ranges = prefix.get_child_ranges()

        self.assertEqual(len(child_ranges), 2)
        self.assertEqual(child_ranges[0], ranges[2])
        self.assertEqual(child_ranges[1], ranges[3])

    def test_get_child_ips(self):
        vrfs = VRF.objects.bulk_create((
            VRF(name='VRF 1'),
            VRF(name='VRF 2'),
            VRF(name='VRF 3'),
        ))
        parent_prefix = Prefix.objects.create(
            prefix=IPNetwork('10.0.0.0/16'), status=PrefixStatusChoices.STATUS_CONTAINER
        )
        ips = IPAddress.objects.bulk_create((
            IPAddress(address=IPNetwork('10.0.0.1/24'), vrf=None),
            IPAddress(address=IPNetwork('10.0.1.1/24'), vrf=vrfs[0]),
            IPAddress(address=IPNetwork('10.0.2.1/24'), vrf=vrfs[1]),
            IPAddress(address=IPNetwork('10.0.3.1/24'), vrf=vrfs[2]),
        ))
        child_ip_pks = {p.pk for p in parent_prefix.get_child_ips()}

        # Global container should return all children
        self.assertSetEqual(child_ip_pks, {ips[0].pk, ips[1].pk, ips[2].pk, ips[3].pk})

        parent_prefix.vrf = vrfs[0]
        parent_prefix.save()
        child_ip_pks = {p.pk for p in parent_prefix.get_child_ips()}

        # VRF container is limited to its own VRF
        self.assertSetEqual(child_ip_pks, {ips[1].pk})

    def test_get_available_prefixes(self):

        prefixes = Prefix.objects.bulk_create((
            Prefix(prefix=IPNetwork('10.0.0.0/16')),  # Parent prefix
            Prefix(prefix=IPNetwork('10.0.0.0/20')),
            Prefix(prefix=IPNetwork('10.0.32.0/20')),
            Prefix(prefix=IPNetwork('10.0.128.0/18')),
        ))
        missing_prefixes = IPSet([
            IPNetwork('10.0.16.0/20'),
            IPNetwork('10.0.48.0/20'),
            IPNetwork('10.0.64.0/18'),
            IPNetwork('10.0.192.0/18'),
        ])
        available_prefixes = prefixes[0].get_available_prefixes()

        self.assertEqual(available_prefixes, missing_prefixes)

    def test_get_available_ips(self):

        parent_prefix = Prefix.objects.create(prefix=IPNetwork('10.0.0.0/28'))
        IPAddress.objects.bulk_create((
            IPAddress(address=IPNetwork('10.0.0.1/26')),
            IPAddress(address=IPNetwork('10.0.0.3/26')),
            IPAddress(address=IPNetwork('10.0.0.5/26')),
            IPAddress(address=IPNetwork('10.0.0.7/26')),
        ))
        IPRange.objects.create(
            start_address=IPNetwork('10.0.0.9/26'),
            end_address=IPNetwork('10.0.0.12/26')
        )
        missing_ips = IPSet([
            '10.0.0.2/32',
            '10.0.0.4/32',
            '10.0.0.6/32',
            '10.0.0.8/32',
            '10.0.0.13/32',
            '10.0.0.14/32',
        ])
        available_ips = parent_prefix.get_available_ips()

        self.assertEqual(available_ips, missing_ips)

    def test_get_first_available_prefix(self):

        prefixes = Prefix.objects.bulk_create((
            Prefix(prefix=IPNetwork('10.0.0.0/16')),  # Parent prefix
            Prefix(prefix=IPNetwork('10.0.0.0/24')),
            Prefix(prefix=IPNetwork('10.0.1.0/24')),
            Prefix(prefix=IPNetwork('10.0.2.0/24')),
        ))
        self.assertEqual(prefixes[0].get_first_available_prefix(), IPNetwork('10.0.3.0/24'))

        Prefix.objects.create(prefix=IPNetwork('10.0.3.0/24'))
        self.assertEqual(prefixes[0].get_first_available_prefix(), IPNetwork('10.0.4.0/22'))

    def test_get_first_available_ip(self):

        parent_prefix = Prefix.objects.create(prefix=IPNetwork('10.0.0.0/24'))
        IPAddress.objects.bulk_create((
            IPAddress(address=IPNetwork('10.0.0.1/24')),
            IPAddress(address=IPNetwork('10.0.0.2/24')),
            IPAddress(address=IPNetwork('10.0.0.3/24')),
        ))
        self.assertEqual(parent_prefix.get_first_available_ip(), '10.0.0.4/24')

        IPAddress.objects.create(address=IPNetwork('10.0.0.4/24'))
        self.assertEqual(parent_prefix.get_first_available_ip(), '10.0.0.5/24')

    def test_get_first_available_ip_ipv6(self):
        parent_prefix = Prefix.objects.create(prefix=IPNetwork('2001:db8:500::/64'))
        self.assertEqual(parent_prefix.get_first_available_ip(), '2001:db8:500::1/64')

    def test_get_first_available_ip_ipv6_rfc3627(self):
        parent_prefix = Prefix.objects.create(prefix=IPNetwork('2001:db8:500:4::/126'))
        self.assertEqual(parent_prefix.get_first_available_ip(), '2001:db8:500:4::1/126')

    def test_get_first_available_ip_ipv6_rfc6164(self):
        parent_prefix = Prefix.objects.create(prefix=IPNetwork('2001:db8:500:5::/127'))
        self.assertEqual(parent_prefix.get_first_available_ip(), '2001:db8:500:5::/127')

    def test_get_utilization_container(self):
        prefixes = (
            Prefix(prefix=IPNetwork('10.0.0.0/24'), status=PrefixStatusChoices.STATUS_CONTAINER),
            Prefix(prefix=IPNetwork('10.0.0.0/26')),
            Prefix(prefix=IPNetwork('10.0.0.128/26')),
        )
        Prefix.objects.bulk_create(prefixes)
        self.assertEqual(prefixes[0].get_utilization(), 50)  # 50% utilization

    def test_get_utilization_noncontainer(self):
        prefix = Prefix.objects.create(
            prefix=IPNetwork('10.0.0.0/24'),
            status=PrefixStatusChoices.STATUS_ACTIVE
        )

        # Create 32 child IPs
        IPAddress.objects.bulk_create([
            IPAddress(address=IPNetwork(f'10.0.0.{i}/24')) for i in range(1, 33)
        ])
        self.assertEqual(prefix.get_utilization(), 32 / 254 * 100)  # ~12.5% utilization

        # Create a child range with 32 additional IPs
        IPRange.objects.create(start_address=IPNetwork('10.0.0.33/24'), end_address=IPNetwork('10.0.0.64/24'))
        self.assertEqual(prefix.get_utilization(), 64 / 254 * 100)  # ~25% utilization

    #
    # Uniqueness enforcement tests
    #

    @override_settings(ENFORCE_GLOBAL_UNIQUE=False)
    def test_duplicate_global(self):
        Prefix.objects.create(prefix=IPNetwork('192.0.2.0/24'))
        duplicate_prefix = Prefix(prefix=IPNetwork('192.0.2.0/24'))
        self.assertIsNone(duplicate_prefix.clean())

    @override_settings(ENFORCE_GLOBAL_UNIQUE=True)
    def test_duplicate_global_unique(self):
        Prefix.objects.create(prefix=IPNetwork('192.0.2.0/24'))
        duplicate_prefix = Prefix(prefix=IPNetwork('192.0.2.0/24'))
        self.assertRaises(ValidationError, duplicate_prefix.clean)

    def test_duplicate_vrf(self):
        vrf = VRF.objects.create(name='Test', rd='1:1', enforce_unique=False)
        Prefix.objects.create(vrf=vrf, prefix=IPNetwork('192.0.2.0/24'))
        duplicate_prefix = Prefix(vrf=vrf, prefix=IPNetwork('192.0.2.0/24'))
        self.assertIsNone(duplicate_prefix.clean())

    def test_duplicate_vrf_unique(self):
        vrf = VRF.objects.create(name='Test', rd='1:1', enforce_unique=True)
        Prefix.objects.create(vrf=vrf, prefix=IPNetwork('192.0.2.0/24'))
        duplicate_prefix = Prefix(vrf=vrf, prefix=IPNetwork('192.0.2.0/24'))
        self.assertRaises(ValidationError, duplicate_prefix.clean)


class TestPrefixHierarchy(TestCase):
    """
    Test the automatic updating of depth and child count in response to changes made within
    the prefix hierarchy.
    """
    @classmethod
    def setUpTestData(cls):

        prefixes = (

            # IPv4
            Prefix(prefix='10.0.0.0/8', _depth=0, _children=2),
            Prefix(prefix='10.0.0.0/16', _depth=1, _children=1),
            Prefix(prefix='10.0.0.0/24', _depth=2, _children=0),

            # IPv6
            Prefix(prefix='2001:db8::/32', _depth=0, _children=2),
            Prefix(prefix='2001:db8::/40', _depth=1, _children=1),
            Prefix(prefix='2001:db8::/48', _depth=2, _children=0),

        )
        Prefix.objects.bulk_create(prefixes)

    def test_create_prefix4(self):
        # Create 10.0.0.0/12
        Prefix(prefix='10.0.0.0/12').save()

        prefixes = Prefix.objects.filter(prefix__family=4)
        self.assertEqual(prefixes[0].prefix, IPNetwork('10.0.0.0/8'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 3)
        self.assertEqual(prefixes[1].prefix, IPNetwork('10.0.0.0/12'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 2)
        self.assertEqual(prefixes[2].prefix, IPNetwork('10.0.0.0/16'))
        self.assertEqual(prefixes[2]._depth, 2)
        self.assertEqual(prefixes[2]._children, 1)
        self.assertEqual(prefixes[3].prefix, IPNetwork('10.0.0.0/24'))
        self.assertEqual(prefixes[3]._depth, 3)
        self.assertEqual(prefixes[3]._children, 0)

    def test_create_prefix6(self):
        # Create 2001:db8::/36
        Prefix(prefix='2001:db8::/36').save()

        prefixes = Prefix.objects.filter(prefix__family=6)
        self.assertEqual(prefixes[0].prefix, IPNetwork('2001:db8::/32'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 3)
        self.assertEqual(prefixes[1].prefix, IPNetwork('2001:db8::/36'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 2)
        self.assertEqual(prefixes[2].prefix, IPNetwork('2001:db8::/40'))
        self.assertEqual(prefixes[2]._depth, 2)
        self.assertEqual(prefixes[2]._children, 1)
        self.assertEqual(prefixes[3].prefix, IPNetwork('2001:db8::/48'))
        self.assertEqual(prefixes[3]._depth, 3)
        self.assertEqual(prefixes[3]._children, 0)

    def test_update_prefix4(self):
        # Change 10.0.0.0/24 to 10.0.0.0/12
        p = Prefix.objects.get(prefix='10.0.0.0/24')
        p.prefix = '10.0.0.0/12'
        p.save()

        prefixes = Prefix.objects.filter(prefix__family=4)
        self.assertEqual(prefixes[0].prefix, IPNetwork('10.0.0.0/8'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 2)
        self.assertEqual(prefixes[1].prefix, IPNetwork('10.0.0.0/12'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 1)
        self.assertEqual(prefixes[2].prefix, IPNetwork('10.0.0.0/16'))
        self.assertEqual(prefixes[2]._depth, 2)
        self.assertEqual(prefixes[2]._children, 0)

    def test_update_prefix6(self):
        # Change 2001:db8::/48 to 2001:db8::/36
        p = Prefix.objects.get(prefix='2001:db8::/48')
        p.prefix = '2001:db8::/36'
        p.save()

        prefixes = Prefix.objects.filter(prefix__family=6)
        self.assertEqual(prefixes[0].prefix, IPNetwork('2001:db8::/32'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 2)
        self.assertEqual(prefixes[1].prefix, IPNetwork('2001:db8::/36'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 1)
        self.assertEqual(prefixes[2].prefix, IPNetwork('2001:db8::/40'))
        self.assertEqual(prefixes[2]._depth, 2)
        self.assertEqual(prefixes[2]._children, 0)

    def test_update_prefix_vrf4(self):
        vrf = VRF(name='VRF A')
        vrf.save()

        # Move 10.0.0.0/16 to a VRF
        p = Prefix.objects.get(prefix='10.0.0.0/16')
        p.vrf = vrf
        p.save()

        prefixes = Prefix.objects.filter(vrf__isnull=True, prefix__family=4)
        self.assertEqual(prefixes[0].prefix, IPNetwork('10.0.0.0/8'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 1)
        self.assertEqual(prefixes[1].prefix, IPNetwork('10.0.0.0/24'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 0)

        prefixes = Prefix.objects.filter(vrf=vrf)
        self.assertEqual(prefixes[0].prefix, IPNetwork('10.0.0.0/16'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 0)

    def test_update_prefix_vrf6(self):
        vrf = VRF(name='VRF A')
        vrf.save()

        # Move 2001:db8::/40 to a VRF
        p = Prefix.objects.get(prefix='2001:db8::/40')
        p.vrf = vrf
        p.save()

        prefixes = Prefix.objects.filter(vrf__isnull=True, prefix__family=6)
        self.assertEqual(prefixes[0].prefix, IPNetwork('2001:db8::/32'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 1)
        self.assertEqual(prefixes[1].prefix, IPNetwork('2001:db8::/48'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 0)

        prefixes = Prefix.objects.filter(vrf=vrf)
        self.assertEqual(prefixes[0].prefix, IPNetwork('2001:db8::/40'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 0)

    def test_delete_prefix4(self):
        # Delete 10.0.0.0/16
        Prefix.objects.filter(prefix='10.0.0.0/16').delete()

        prefixes = Prefix.objects.filter(prefix__family=4)
        self.assertEqual(prefixes[0].prefix, IPNetwork('10.0.0.0/8'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 1)
        self.assertEqual(prefixes[1].prefix, IPNetwork('10.0.0.0/24'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 0)

    def test_delete_prefix6(self):
        # Delete 2001:db8::/40
        Prefix.objects.filter(prefix='2001:db8::/40').delete()

        prefixes = Prefix.objects.filter(prefix__family=6)
        self.assertEqual(prefixes[0].prefix, IPNetwork('2001:db8::/32'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 1)
        self.assertEqual(prefixes[1].prefix, IPNetwork('2001:db8::/48'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 0)

    def test_duplicate_prefix4(self):
        # Duplicate 10.0.0.0/16
        Prefix(prefix='10.0.0.0/16').save()

        prefixes = Prefix.objects.filter(prefix__family=4)
        self.assertEqual(prefixes[0].prefix, IPNetwork('10.0.0.0/8'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 3)
        self.assertEqual(prefixes[1].prefix, IPNetwork('10.0.0.0/16'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 1)
        self.assertEqual(prefixes[2].prefix, IPNetwork('10.0.0.0/16'))
        self.assertEqual(prefixes[2]._depth, 1)
        self.assertEqual(prefixes[2]._children, 1)
        self.assertEqual(prefixes[3].prefix, IPNetwork('10.0.0.0/24'))
        self.assertEqual(prefixes[3]._depth, 2)
        self.assertEqual(prefixes[3]._children, 0)

    def test_duplicate_prefix6(self):
        # Duplicate 2001:db8::/40
        Prefix(prefix='2001:db8::/40').save()

        prefixes = Prefix.objects.filter(prefix__family=6)
        self.assertEqual(prefixes[0].prefix, IPNetwork('2001:db8::/32'))
        self.assertEqual(prefixes[0]._depth, 0)
        self.assertEqual(prefixes[0]._children, 3)
        self.assertEqual(prefixes[1].prefix, IPNetwork('2001:db8::/40'))
        self.assertEqual(prefixes[1]._depth, 1)
        self.assertEqual(prefixes[1]._children, 1)
        self.assertEqual(prefixes[2].prefix, IPNetwork('2001:db8::/40'))
        self.assertEqual(prefixes[2]._depth, 1)
        self.assertEqual(prefixes[2]._children, 1)
        self.assertEqual(prefixes[3].prefix, IPNetwork('2001:db8::/48'))
        self.assertEqual(prefixes[3]._depth, 2)
        self.assertEqual(prefixes[3]._children, 0)


class TestIPAddress(TestCase):

    def test_get_duplicates(self):
        ips = IPAddress.objects.bulk_create((
            IPAddress(address=IPNetwork('192.0.2.1/24')),
            IPAddress(address=IPNetwork('192.0.2.1/24')),
            IPAddress(address=IPNetwork('192.0.2.1/24')),
        ))
        duplicate_ip_pks = [p.pk for p in ips[0].get_duplicates()]

        self.assertSetEqual(set(duplicate_ip_pks), {ips[1].pk, ips[2].pk})

    #
    # Uniqueness enforcement tests
    #

    @override_settings(ENFORCE_GLOBAL_UNIQUE=False)
    def test_duplicate_global(self):
        IPAddress.objects.create(address=IPNetwork('192.0.2.1/24'))
        duplicate_ip = IPAddress(address=IPNetwork('192.0.2.1/24'))
        self.assertIsNone(duplicate_ip.clean())

    @override_settings(ENFORCE_GLOBAL_UNIQUE=True)
    def test_duplicate_global_unique(self):
        IPAddress.objects.create(address=IPNetwork('192.0.2.1/24'))
        duplicate_ip = IPAddress(address=IPNetwork('192.0.2.1/24'))
        self.assertRaises(ValidationError, duplicate_ip.clean)

    def test_duplicate_vrf(self):
        vrf = VRF.objects.create(name='Test', rd='1:1', enforce_unique=False)
        IPAddress.objects.create(vrf=vrf, address=IPNetwork('192.0.2.1/24'))
        duplicate_ip = IPAddress(vrf=vrf, address=IPNetwork('192.0.2.1/24'))
        self.assertIsNone(duplicate_ip.clean())

    def test_duplicate_vrf_unique(self):
        vrf = VRF.objects.create(name='Test', rd='1:1', enforce_unique=True)
        IPAddress.objects.create(vrf=vrf, address=IPNetwork('192.0.2.1/24'))
        duplicate_ip = IPAddress(vrf=vrf, address=IPNetwork('192.0.2.1/24'))
        self.assertRaises(ValidationError, duplicate_ip.clean)

    @override_settings(ENFORCE_GLOBAL_UNIQUE=True)
    def test_duplicate_nonunique_nonrole_role(self):
        IPAddress.objects.create(address=IPNetwork('192.0.2.1/24'))
        duplicate_ip = IPAddress(address=IPNetwork('192.0.2.1/24'), role=IPAddressRoleChoices.ROLE_VIP)
        self.assertRaises(ValidationError, duplicate_ip.clean)

    @override_settings(ENFORCE_GLOBAL_UNIQUE=True)
    def test_duplicate_nonunique_role_nonrole(self):
        IPAddress.objects.create(address=IPNetwork('192.0.2.1/24'), role=IPAddressRoleChoices.ROLE_VIP)
        duplicate_ip = IPAddress(address=IPNetwork('192.0.2.1/24'))
        self.assertRaises(ValidationError, duplicate_ip.clean)

    @override_settings(ENFORCE_GLOBAL_UNIQUE=True)
    def test_duplicate_nonunique_role(self):
        IPAddress.objects.create(address=IPNetwork('192.0.2.1/24'), role=IPAddressRoleChoices.ROLE_VIP)
        IPAddress.objects.create(address=IPNetwork('192.0.2.1/24'), role=IPAddressRoleChoices.ROLE_VIP)


class TestVLANGroup(TestCase):

    @classmethod
    def setUpTestData(cls):
        vlangroup = VLANGroup.objects.create(
            name='VLAN Group 1',
            slug='vlan-group-1',
            min_vid=100,
            max_vid=199
        )
        VLAN.objects.bulk_create((
            VLAN(name='VLAN 100', vid=100, group=vlangroup),
            VLAN(name='VLAN 101', vid=101, group=vlangroup),
            VLAN(name='VLAN 102', vid=102, group=vlangroup),
            VLAN(name='VLAN 103', vid=103, group=vlangroup),
        ))

    def test_get_available_vids(self):
        vlangroup = VLANGroup.objects.first()
        child_vids = VLAN.objects.filter(group=vlangroup).values_list('vid', flat=True)
        self.assertEqual(len(child_vids), 4)

        available_vids = vlangroup.get_available_vids()
        self.assertListEqual(available_vids, list(range(104, 200)))

    def test_get_next_available_vid(self):
        vlangroup = VLANGroup.objects.first()
        self.assertEqual(vlangroup.get_next_available_vid(), 104)

        VLAN.objects.create(name='VLAN 104', vid=104, group=vlangroup)
        self.assertEqual(vlangroup.get_next_available_vid(), 105)


class TestL2VPNTermination(TestCase):

    @classmethod
    def setUpTestData(cls):

        site = Site.objects.create(name='Site 1')
        manufacturer = Manufacturer.objects.create(name='Manufacturer 1')
        device_type = DeviceType.objects.create(model='Device Type 1', manufacturer=manufacturer)
        device_role = DeviceRole.objects.create(name='Switch')
        device = Device.objects.create(
            name='Device 1',
            site=site,
            device_type=device_type,
            device_role=device_role,
            status='active'
        )

        interfaces = (
            Interface(name='Interface 1', device=device, type='1000baset'),
            Interface(name='Interface 2', device=device, type='1000baset'),
            Interface(name='Interface 3', device=device, type='1000baset'),
            Interface(name='Interface 4', device=device, type='1000baset'),
            Interface(name='Interface 5', device=device, type='1000baset'),
        )

        Interface.objects.bulk_create(interfaces)

        vlans = (
            VLAN(name='VLAN 1', vid=651),
            VLAN(name='VLAN 2', vid=652),
            VLAN(name='VLAN 3', vid=653),
            VLAN(name='VLAN 4', vid=654),
            VLAN(name='VLAN 5', vid=655),
            VLAN(name='VLAN 6', vid=656),
            VLAN(name='VLAN 7', vid=657)
        )

        VLAN.objects.bulk_create(vlans)

        l2vpns = (
            L2VPN(name='L2VPN 1', type='vxlan', identifier=650001),
            L2VPN(name='L2VPN 2', type='vpws', identifier=650002),
            L2VPN(name='L2VPN 3', type='vpls'),  # No RD
        )
        L2VPN.objects.bulk_create(l2vpns)

        l2vpnterminations = (
            L2VPNTermination(l2vpn=l2vpns[0], assigned_object=vlans[0]),
            L2VPNTermination(l2vpn=l2vpns[0], assigned_object=vlans[1]),
            L2VPNTermination(l2vpn=l2vpns[0], assigned_object=vlans[2])
        )

        L2VPNTermination.objects.bulk_create(l2vpnterminations)

    def test_duplicate_interface_terminations(self):
        device = Device.objects.first()
        interface = Interface.objects.filter(device=device).first()
        l2vpn = L2VPN.objects.first()

        L2VPNTermination.objects.create(l2vpn=l2vpn, assigned_object=interface)
        duplicate = L2VPNTermination(l2vpn=l2vpn, assigned_object=interface)

        self.assertRaises(ValidationError, duplicate.clean)

    def test_duplicate_vlan_terminations(self):
        vlan = Interface.objects.first()
        l2vpn = L2VPN.objects.first()

        L2VPNTermination.objects.create(l2vpn=l2vpn, assigned_object=vlan)
        duplicate = L2VPNTermination(l2vpn=l2vpn, assigned_object=vlan)
        self.assertRaises(ValidationError, duplicate.clean)
