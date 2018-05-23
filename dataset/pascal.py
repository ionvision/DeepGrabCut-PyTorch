import os
import torch
from PIL import Image
import cv2
import numpy as np
from torch.utils.data import Dataset, sampler
from natsort import natsorted
from mypath import Path
import json

class PascalVocDataset(Dataset):
    """
    PascalVoc dataset
    """

    def __init__(self, base_dir=Path.db_root_dir('pascal'), image_size=None, split='train', transform=None,
                 area_thres=0, preprocess=False, default=False, retname=True):
        """

        :param base_dir: path to DAVIS dataset directory
        :param image_size: (width, height) tuple to resize the image
        :param year: which train/val split of DAVIS to use
        :param phase: train/val
        :param transform: transform to apply
        """
        super().__init__()
        self._base_dir = base_dir
        self._image_size = image_size
        self._image_dir = os.path.join(self._base_dir, 'JPEGImages')
        self._annotation_dir = os.path.join(self._base_dir, 'SegmentationGT')
        self._mask_dir = os.path.join(self._base_dir, 'SegmentationObject')
        self._cat_dir = os.path.join(self._base_dir, 'SegmentationClass')
        self._dismaps_dir = os.path.join(self._base_dir, 'DistanceMaps')

        self.area_thres = area_thres
        self.default = default
        self.retname = retname

        if isinstance(split, str):
            self.split = [split]
        else:
            split.sort()
            self.split = split

        # Build the ids file
        area_th_str = ""
        if self.area_thres != 0:
            area_th_str = '_area_thres-' + str(area_thres)

        self.obj_list_file = os.path.join(self._base_dir, 'ImageSets', 'Segmentation',
                                             '_'.join(self.split) + '_instances' + area_th_str + '.txt')
        self.transform = transform

        _splits_dir = os.path.join(self._base_dir, 'ImageSets', 'Segmentation')

        self.im_ids = []
        self.images = []
        self.categories = []
        self.masks = []

        for splt in self.split:
            with open(os.path.join(os.path.join(_splits_dir, splt + '.txt')), "r") as f:
                lines = f.read().splitlines()

            for ii, line in enumerate(lines):
                _image = os.path.join(self._image_dir, line + ".jpg")
                _cat = os.path.join(self._cat_dir, line + ".png")
                _mask = os.path.join(self._mask_dir, line + ".png")
                assert os.path.isfile(_image)
                assert os.path.isfile(_cat)
                assert os.path.isfile(_mask)
                self.im_ids.append(line.rstrip('\n'))
                self.images.append(_image)
                self.categories.append(_cat)
                self.masks.append(_mask)

        assert (len(self.images) == len(self.masks))
        assert (len(self.images) == len(self.categories))

        # Precompute the list of objects and their categories for each image
        if (not self._check_preprocess()) or preprocess:
            print('Preprocessing of PASCAL VOC dataset, this will take long, but it will be done only once.')
            self._preprocess()

        # Build the list of objects
        self.obj_list = []
        num_images = 0
        for ii in range(len(self.im_ids)):
            flag = False
            for jj in range(len(self.obj_dict[self.im_ids[ii]])):
                if self.obj_dict[self.im_ids[ii]][jj] != -1:
                    self.obj_list.append([ii, jj])
                    flag = True
            if flag:
                num_images += 1

        # Display stats
        print('Number of images: {:d}\nNumber of objects: {:d}'.format(num_images, len(self.obj_list)))

        self._make_img_gt_point_pair(1)

    def __len__(self):
        return len(self.obj_list)


    def __getitem__(self, index):
        _img, _target, _void_pixels, _, _, _ = self._make_img_gt_point_pair(index)
        sample = {'image': _img, 'gt': _target, 'void_pixels': _void_pixels}

        if self.retname: # return meta information
            _im_ii = self.obj_list[index][0]
            _obj_ii = self.obj_list[index][1]
            sample['meta'] = {'image': str(self.im_ids[_im_ii]),
                              'object': str(_obj_ii),
                              'category': self.obj_dict[self.im_ids[_im_ii]][_obj_ii],
                              'im_size': (_img.shape[0], _img.shape[1])}

        if self.transform is not None:
            sample = self.transform(sample)

        return sample

    def _check_preprocess(self):
        _obj_list_file = self.obj_list_file
        if not os.path.isfile(_obj_list_file):
            return False
        else:
            self.obj_dict = json.load(open(_obj_list_file, 'r'))

            return list(np.sort([str(x) for x in self.obj_dict.keys()])) == list(np.sort(self.im_ids))

    def _preprocess(self):
        self.obj_dict = {}
        obj_counter = 0
        for ii in range(len(self.im_ids)):
            # Read object masks and get number of objects
            _mask = np.array(Image.open(self.masks[ii]))
            _mask_ids = np.unique(_mask)
            if _mask_ids[-1] == 255:
                n_obj = _mask_ids[-2]
            else:
                n_obj = _mask_ids[-1]

            # Get the categories from these objects
            _cats = np.array(Image.open(self.categories[ii]))
            _cat_ids = []
            for jj in range(n_obj):
                tmp = np.where(_mask == jj + 1)
                obj_area = len(tmp[0])
                if obj_area > self.area_thres:
                    _cat_ids.append(int(_cats[tmp[0][0], tmp[1][0]]))
                else:
                    _cat_ids.append(-1)
                obj_counter += 1

            self.obj_dict[self.im_ids[ii]] = _cat_ids

        with open(self.obj_list_file, 'w') as outfile:
            outfile.write('{{\n\t"{:s}": {:s}'.format(self.im_ids[0], json.dumps(self.obj_dict[self.im_ids[0]])))
            for ii in range(1, len(self.im_ids)):
                outfile.write(',\n\t"{:s}": {:s}'.format(self.im_ids[ii], json.dumps(self.obj_dict[self.im_ids[ii]])))
            outfile.write('\n}\n')

        print('Preprocessing finished')

    def _make_img_gt_point_pair(self, index):
        _im_ii = self.obj_list[index][0]
        _obj_ii = self.obj_list[index][1]

        # Read Image
        _img = np.array(Image.open(self.images[_im_ii]).convert('RGB')).astype(np.float32)

        # Read Target object
        _tmp = (np.array(Image.open(self.masks[_im_ii]))).astype(np.float32)
        _void_pixels = (_tmp == 255) # ignore label == 255, it is boundary pixel
        _tmp[_void_pixels] = 0

        _other_same_class = np.zeros(_tmp.shape)
        _other_classes = np.zeros(_tmp.shape)

        if self.default:
            _target = _tmp
            _background = np.logical_and(_tmp == 0, ~_void_pixels) # background is where label == 0 except boundary pixel
        else:
            _target = (_tmp == (_obj_ii + 1)).astype(np.float32) # mask a certain object, other pixel is zero
            _background = np.logical_and(_tmp == 0, ~_void_pixels) # background is where label == 0 except boundary pixel
            obj_cat = self.obj_dict[self.im_ids[_im_ii]][_obj_ii] # object label
            for ii in range(1, np.max(_tmp).astype(np.int)+1): # 1, ..., num(instances)
                ii_cat = self.obj_dict[self.im_ids[_im_ii]][ii-1] # instance's category
                if obj_cat == ii_cat and ii != _obj_ii+1:
                    _other_same_class = np.logical_or(_other_same_class, _tmp == ii)
                elif ii != _obj_ii+1:
                    _other_classes = np.logical_or(_other_classes, _tmp == ii)

        return _img, _target, _void_pixels.astype(np.float32), \
               _other_classes.astype(np.float32), _other_same_class.astype(np.float32), \
               _background.astype(np.float32)

    def __str__(self):
        return 'VOC2012(split=' + str(self.split) + ',area_thres=' + str(self.area_thres) + ')'
# Transforms
class ToTensor(object):
    """Convert ndarrays in sample to Tensors."""

    def __call__(self, sample):
        # swap color axis because
        # numpy image: H x W x C
        # torch image: C X H X W
        return {'input': torch.from_numpy(sample['input'].transpose((2, 0, 1))),
                'annotation': torch.from_numpy(sample['annotation'].astype(np.uint8))
                }
