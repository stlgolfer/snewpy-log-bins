from pathlib import Path
import jinja2
import pandas as pd
import numpy as np
import os

import logging
#logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from dataclasses import dataclass
import subprocess

class SNOwGLoBES:
    def __init__(self, base_dir=None):
        """ SNOwGLoBES interface """
        if not base_dir:
            base_dir = os.environ['SNOWGLOBES']
        self.base_dir = Path(base_dir)
        self._load_detectors(self.base_dir/'detector_configurations.dat')
        self._load_channels(self.base_dir/'channels')
        self._load_efficiencies(self.base_dir/'effic')

        env = jinja2.Environment(loader=jinja2.PackageLoader('snewpy'))
        self.template = env.get_template('supernova.glb')

    def _load_detectors(self, path):
        df = pd.read_table(path,names=['name','mass','factor'], delim_whitespace=True, comment='#')
        df['tgt_mass']=df.mass*df.factor
        self.detectors=df.set_index('name').T
        logger.info(f'read masses for detectors {self.detectors}')
        logger.debug(f'detectors: {self.detectors}')
       
    def _load_channels(self, chan_dir):
        self.channels = {}
        all_channels = []
        for f in chan_dir.glob('channels_*.dat'):
            material = f.stem[len('channels_'):]
            df = pd.read_table(f,delim_whitespace=True, index_col=1, comment='%', names=['name','n','parity','flavor','weight'])
            self.channels[material] = df
        self.materials = list(self.channels.keys())
        self.chan_dir = chan_dir
        logger.info(f'read channels for  materials: {self.materials}')
        logger.debug(f'channels: {self.channels}')
        
    def _load_efficiencies(self, path):
        result = {}
        for detector in self.detectors:
            res_det = {}
            for file in path.glob(f'effic_*_{detector}.dat'):
                channel =  file.stem[len('effic_'):-len(detector)-1]
                logger.debug(f'Reading file ({detector},{channel}): {file}')
                with open(file) as f:
                    res_det[channel]= f.read()
            result[detector]=res_det
        self.efficiencies = result 
        logger.info(f'read efficiencies for materials: {list(self.efficiencies.keys())}')
        logger.debug(f'efficiencies: {self.efficiencies}')
       
    def run(self, flux_file, detector, material):
        assert material in self.materials
        assert detector in self.detectors
        assert detector in self.efficiencies

        assert Path(flux_file).exists()
        return Runner(self,Path(flux_file),detector,material).run()

@dataclass    
class Runner:
    sng: SNOwGLoBES
    flux_file: Path
    detector: str
    material: str
    
    def __post_init__(self):
        self.channels=self.sng.channels[self.material]
        self.efficiency=self.sng.efficiencies[self.detector]
        self.det_config=self.sng.detectors[self.detector]
        self.base_dir=self.sng.base_dir
        self.out_dir=self.base_dir/'out'
    def _generate_globes_config(self):
        cfg =  self.sng.template.render(flux_file=self.flux_file.resolve(),
                                    detector=self.detector,
                                    target_mass=self.det_config.tgt_mass,
                                    smear_dir=self.base_dir/'smear',
                                    xsec_dir =self.base_dir/'xscns',
                                    channels =list(self.channels.itertuples()),
                                    efficiency =self.efficiency)
        return cfg

    def _parse_output(self, output):
        data = {}
        for l in output:
            #read the generated files from output
            if l.endswith('weighted.dat'):
                channum, fname = l.split()
                fname = self.base_dir/fname
                #load the data from file
                try:
                    E,N = np.loadtxt(fname, comments=['--','Total'], unpack=True)
                finally:
                    fname.unlink() #cleanup file
                channel=self.channels.loc[int(channum)]
                smeared= 'smeared'  if '_smeared' in fname.stem else 'unsmeared'
                data[(channel['name'],smeared,'unweighted')] = N
                data[(channel['name'],smeared,'weighted')] = N*channel['weight']
        #collect everything to pandas DataFrame
        df = pd.DataFrame(data, index = E)
        df.index.rename('E', inplace=True)
        df.columns.rename(['channel','is_smeared','is_weighted'], inplace=True)
        return df.reorder_levels([2,1,0], axis='columns')

    def run(self):
        """write configuration file and run snowglobes"""
        cfg = self._generate_globes_config()
        chan_file = self.sng.chan_dir/f'channels_{self.material}.dat'
        #write configuration file:
        with open(self.base_dir/'supernova.glb','w') as f:
            f.write(cfg)

        r = subprocess.run(['bin/supernova', self.flux_file.stem, chan_file, self.detector],
                cwd=self.base_dir,check=True, capture_output=True)
        output = r.stdout.decode('utf_8').split('\n') 
        logger.debug(output)
        if(r.stderr):
            logger.error(r.stderr.decode('utf_8'))
        tables = self._parse_output(output)
        return tables


